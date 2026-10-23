"""Pages de travail inspirées des maquettes : recherche, prétraitement,
segmentation U-Net et cartes. Elles s'appuient sur les mêmes données que le
reste de la plateforme (base, archives, résultats) : aucune valeur fictive."""
import json
import re
from pathlib import Path

from flask import (Blueprint, abort, current_app, g, redirect, render_template, request,
                   send_file, url_for)

from app.modeles_bd import bd
from app.routes.auth import connexion_requise

bp = Blueprint("vues", __name__)
FICHIERS_CONTROLE = re.compile(r"^(bande_(B2|B3|B4|B8|B11|B12)\.png)$")


def _produits():
    return bd().execute("SELECT * FROM produit WHERE utilisateur_id = ? ORDER BY date_acquisition DESC, id DESC",
                        (g.utilisateur["id"],)).fetchall()


def _analyses_terminees():
    return bd().execute(
        "SELECT a.*, p.date_acquisition, p.tuile, p.nom AS nom_produit, p.chemin AS chemin_produit "
        "FROM analyse a JOIN produit p ON p.id = a.produit_id WHERE a.utilisateur_id = ? "
        "AND a.statut = 'terminee' ORDER BY p.date_acquisition DESC, a.id DESC", (g.utilisateur["id"],)).fetchall()


def _entier(nom):
    try:
        return int(request.args.get(nom, ""))
    except ValueError:
        return None


# ---------------------------------------------------------------------
# Recherche (barre du haut)
# ---------------------------------------------------------------------
@bp.route("/recherche")
@connexion_requise
def recherche():
    q = request.args.get("q", "").strip()
    uid = g.utilisateur["id"]
    resultats = []
    if q:
        motif = f"%{q}%"
        for p in bd().execute("SELECT * FROM produit WHERE utilisateur_id = ? AND (date_acquisition LIKE ? "
                              "OR nom LIKE ? OR tuile LIKE ?) ORDER BY date_acquisition DESC",
                              (uid, motif, motif, motif)):
            resultats.append(("Image Sentinel-2", f"{p['date_acquisition']} · tuile {p['tuile']}",
                              url_for("vues.pretraitement", produit=p["id"])))
        numero = int(q) if q.isdigit() else -1
        for a in bd().execute("SELECT a.id, a.statut, p.date_acquisition FROM analyse a JOIN produit p "
                              "ON p.id = a.produit_id WHERE a.utilisateur_id = ? AND (a.id = ? OR "
                              "p.date_acquisition LIKE ?) ORDER BY a.id DESC", (uid, numero, motif)):
            resultats.append(("Analyse", f"n° {a['id']} · image du {a['date_acquisition']}",
                              url_for("vues.segmentation", analyse=a["id"])))
        for s in bd().execute("SELECT id, resume FROM suivi WHERE utilisateur_id = ? AND statut = 'terminee'", (uid,)):
            r = json.loads(s["resume"])
            texte = f"n° {s['id']} · {r['dates'][0]} → {r['dates'][-1]}"
            if str(s["id"]) == q or q in texte:
                resultats.append(("Analyse temporelle", texte, url_for("statistiques.page", suivi=s["id"])))
        for d in bd().execute("SELECT * FROM detection WHERE utilisateur_id = ?", (uid,)):
            texte = f"n° {d['id']} · {d['annee_debut']}–{d['annee_fin']}"
            if str(d["id"]) == q or q in texte:
                resultats.append(("Détection", texte, url_for("detection.resultat", detection_id=d["id"])))
    return render_template("recherche.html", q=q, resultats=resultats)


# ---------------------------------------------------------------------
# Prétraitement (maquette 4)
# ---------------------------------------------------------------------
def _dossier_controle(produit_id):
    return Path(current_app.config["DOSSIER_IMPORTS"]) / str(g.utilisateur["id"]) / "controle" / str(produit_id)


def controle(produit):
    """Contrôle de l'image, calculé une fois puis conservé."""
    dossier = _dossier_controle(produit["id"])
    chemin = dossier / "controle.json"
    if chemin.exists():
        with open(chemin, encoding="utf-8") as f:
            return json.load(f), None
    try:
        from moteur.apercu import controler_produit
        return controler_produit(produit["chemin"], dossier), None
    except Exception as e:
        return None, f"Contrôle impossible : {e}"


@bp.route("/pretraitement")
@connexion_requise
def pretraitement():
    from moteur import config_modele as cm
    produits = _produits()
    if not produits:
        return render_template("pretraitement.html", produits=[], produit=None)
    numero = _entier("produit")
    produit = next((p for p in produits if p["id"] == numero), produits[0])
    stats, erreur = controle(produit)
    h, w = stats["taille_pixels"] if stats else (10980, 10980)
    pas = cm.TAILLE_FENETRE - 32
    nb_fenetres = (-(-(h - cm.TAILLE_FENETRE) // pas) + 1) * (-(-(w - cm.TAILLE_FENETRE) // pas) + 1)
    if stats is None or stats["part_nuages"] is None:
        qualite = None
    elif stats["part_nuages"] < 10:
        qualite = ("bon", "Bonne qualité")
    elif stats["part_nuages"] < 30:
        qualite = ("moyen", "Qualité moyenne : une partie de l'image sera non classée")
    else:
        qualite = ("mauvais", "Image trop nuageuse : choisir une autre date")
    return render_template(
        "pretraitement.html", produits=produits, produit=produit, stats=stats, erreur=erreur, cm=cm,
        qualite=qualite, nb_fenetres=nb_fenetres, hors_tuiles=produit["tuile"] not in cm.TUILES_ENTRAINEMENT,
        bandes_apercu=[("B2", "Bleu"), ("B3", "Vert"), ("B4", "Rouge"), ("B8", "Proche infrarouge"),
                       ("B11", "SWIR 1"), ("B12", "SWIR 2")])


@bp.route("/pretraitement/<int:produit_id>/<nom>")
@connexion_requise
def fichier_controle(produit_id, nom):
    from app.routes.analyses import produit_ou_404
    produit_ou_404(produit_id)
    if not FICHIERS_CONTROLE.match(nom):
        abort(404)
    chemin = _dossier_controle(produit_id) / nom
    if not chemin.exists():
        abort(404)
    return send_file(chemin, mimetype="image/png", max_age=86400)


# ---------------------------------------------------------------------
# Segmentation U-Net (maquette 5)
# ---------------------------------------------------------------------
@bp.route("/segmentation")
@connexion_requise
def segmentation():
    produits = _produits()
    analyses = _analyses_terminees()
    numero = _entier("analyse")
    choisie = next((a for a in analyses if a["id"] == numero), None)
    produit_id = _entier("produit")
    if choisie is None and produit_id:
        choisie = next((a for a in analyses if a["produit_id"] == produit_id), None)
    if choisie is None and analyses and not produit_id:
        choisie = analyses[0]
    produit_choisi = produit_id or (choisie["produit_id"] if choisie else (produits[0]["id"] if produits else None))
    en_cours = bd().execute(
        "SELECT a.*, p.date_acquisition FROM analyse a JOIN produit p ON p.id = a.produit_id "
        "WHERE a.utilisateur_id = ? AND a.statut IN ('en_attente', 'en_cours') ORDER BY a.id",
        (g.utilisateur["id"],)).fetchall()
    resume = json.loads(choisie["resume"]) if choisie else None
    return render_template("segmentation.html", produits=produits, analyses=analyses, choisie=choisie,
                           resume=resume, produit_choisi=produit_choisi, en_cours=en_cours)


# ---------------------------------------------------------------------
# Cartes & Visualisation (maquette 6)
# ---------------------------------------------------------------------
@bp.route("/cartes")
@connexion_requise
def cartes():
    analyses = _analyses_terminees()
    if not analyses:
        return render_template("cartes.html", analyses=[], choisie=None)
    numero = _entier("analyse")
    choisie = next((a for a in analyses if a["id"] == numero), analyses[0])
    # Années comparables : même tuile et même zone que la carte affichée
    comparables = [a for a in analyses if a["zone"] == choisie["zone"] and a["tuile"] == choisie["tuile"]]
    numero_cmp = _entier("comparer")
    autre = next((a for a in comparables if a["id"] == numero_cmp and a["id"] != choisie["id"]), None)
    if autre is None:
        autre = next((a for a in reversed(comparables) if a["id"] != choisie["id"]), None)
    resume = json.loads(choisie["resume"])
    ndvi = (Path(choisie["dossier"]) / "ndvi.tif").exists()
    return render_template("cartes.html", analyses=analyses, choisie=choisie, resume=resume,
                           comparables=comparables, autre=autre, ndvi=ndvi)


@bp.route("/cartes/<int:analyse_id>/ndvi.png")
@connexion_requise
def ndvi_png(analyse_id):
    from app.routes.analyses import analyse_ou_404
    analyse = analyse_ou_404(analyse_id)
    source = Path(analyse["dossier"]) / "ndvi.tif"
    cible = Path(analyse["dossier"]) / "ndvi.png"
    if not source.exists():
        abort(404)
    if not cible.exists():
        from moteur.apercu import ecrire_png_ndvi
        try:
            ecrire_png_ndvi(source, cible)
        except Exception:
            abort(404)
    return send_file(cible, mimetype="image/png", max_age=3600)
