"""Images satellitaires : catalogue des produits Sentinel-2 importés,
avec filtres, aperçu en couleurs réelles et état de traitement."""
import json
from pathlib import Path

from flask import Blueprint, abort, current_app, g, jsonify, render_template, request, send_file

from app.modeles_bd import bd
from app.routes.analyses import produit_ou_404
from app.routes.auth import connexion_requise
from moteur.config_modele import EMPRISE_ENTRAINEMENT, LIBELLES_SAISONS, saison

bp = Blueprint("images", __name__)

ETATS = {"analysee": "Analysée", "a_analyser": "Pas encore analysée", "avertissement": "Avec avertissement"}


def _lignes(uid):
    """Chaque produit avec sa dernière analyse terminée (s'il y en a une)."""
    produits = bd().execute(
        "SELECT * FROM produit WHERE utilisateur_id = ? ORDER BY date_acquisition DESC, id DESC",
        (uid,)).fetchall()
    resultat = []
    for p in produits:
        analyses = bd().execute(
            "SELECT id, statut, resume FROM analyse WHERE produit_id = ? ORDER BY id DESC",
            (p["id"],)).fetchall()
        terminee = next((a for a in analyses if a["statut"] == "terminee"), None)
        r = json.loads(terminee["resume"]) if terminee else None
        s = saison(p["date_acquisition"])
        avertissements = [a for a in p["avertissements"].split("\n") if a]
        etat = ("avertissement" if avertissements else "analysee" if terminee else "a_analyser")
        resultat.append({
            "p": p, "annee": p["date_acquisition"][:4], "saison": s,
            "libelle_saison": LIBELLES_SAISONS[s].split(" (")[0],
            "nb_analyses": len(analyses), "derniere": terminee,
            # Nuages mesurés seulement sur la zone analysée (couche SCL de l'ESA)
            "non_classe": r["statistiques"]["part_non_classee"] if r else None,
            "surface_analysee": r["surface_km2"] if r else None,
            "hors_validite": r.get("part_hors_entrainement") if r else None,
            "etat": etat, "avertissements": avertissements,
        })
    return resultat


PAR_PAGE = 10
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]


@bp.route("/images")
@connexion_requise
def liste():
    """Données Sentinel-2 (maquette 3) : tableau filtrable et paginé."""
    from app.routes.vues import _dossier_controle
    toutes = _lignes(g.utilisateur["id"])
    for l in toutes:
        chemin = _dossier_controle(l["p"]["id"]) / "controle.json"
        l["controle"] = json.load(open(chemin, encoding="utf-8")) if chemin.exists() else None
    filtres = {k: request.args.get(k, "") for k in ("tuile", "annee", "mois", "etat", "q")}
    lignes = [l for l in toutes
              if (not filtres["tuile"] or l["p"]["tuile"] == filtres["tuile"])
              and (not filtres["annee"] or l["annee"] == filtres["annee"])
              and (not filtres["mois"] or l["p"]["date_acquisition"][5:7] == filtres["mois"])
              and (not filtres["etat"] or l["etat"] == filtres["etat"])
              and (not filtres["q"] or filtres["q"].lower() in (l["p"]["nom"] + l["p"]["date_acquisition"]).lower())]
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    nb_pages = max(1, -(-len(lignes) // PAR_PAGE))
    page = min(page, nb_pages)
    comptes = {e: sum(1 for l in toutes if l["etat"] == e) for e in ETATS}
    from moteur.config_modele import BANDES_MODELE
    return render_template(
        "images.html", lignes=lignes[(page - 1) * PAR_PAGE:page * PAR_PAGE], nb_lignes=len(lignes),
        page=page, nb_pages=nb_pages, total=len(toutes), comptes=comptes, etats=ETATS, filtres=filtres,
        tuiles=sorted({l["p"]["tuile"] for l in toutes}), annees=sorted({l["annee"] for l in toutes}, reverse=True),
        mois=[(f"{i + 1:02d}", m) for i, m in enumerate(MOIS)],
        bandes=" ".join(sorted({b for _, b, _ in BANDES_MODELE})))


@bp.route("/images/<int:produit_id>/controle.json")
@connexion_requise
def controle_json(produit_id):
    """Contrôle de l'image (nuages SCL), calculé à la demande pour le tableau."""
    from app.routes.vues import controle
    stats, erreur = controle(produit_ou_404(produit_id))
    if stats is None:
        return jsonify(ok=False, message=erreur)
    return jsonify(ok=True, nuages=stats["part_nuages"], valide=stats["part_valide"])


@bp.route("/images/<int:produit_id>/apercu.jpg")
@connexion_requise
def apercu(produit_id):
    """Aperçu de la tuile entière, créé à la première demande puis conservé."""
    produit = produit_ou_404(produit_id)
    dossier = Path(current_app.config["DOSSIER_IMPORTS"]) / str(g.utilisateur["id"]) / "apercus"
    chemin = dossier / f"{produit_id}.jpg"
    if not chemin.exists():
        from moteur.apercu import ecrire_apercu_tuile
        dossier.mkdir(parents=True, exist_ok=True)
        try:
            ecrire_apercu_tuile(produit["chemin"], chemin)
        except Exception:
            abort(404)
    return send_file(chemin, mimetype="image/jpeg", max_age=86400)
