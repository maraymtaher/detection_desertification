"""Détection : page centrale du système.

L'utilisateur choisit une zone, un mois et une période ; le système retient
une image par année, puis enchaîne tout seul : segmentation U-Net de chaque
image, suivi temporel (classes + NDVI), zones à surveiller. Les résultats
s'affichent sur la même page."""
import json
from collections import Counter

from flask import (Blueprint, abort, flash, g, jsonify, redirect, render_template,
                   request, url_for)

import config
from app import geometrie, graphiques, taches
from app.indicateurs import AVIS, reponse_suivi, zones_du_suivi
from app.modeles_bd import bd
from app.routes.analyses import emprise_du_produit
from app.routes.auth import connexion_requise

bp = Blueprint("detection", __name__)

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]
MIN_IMAGES = 2           # minimum pour comparer
CONSEIL_IMAGES = 5       # minimum pour les tendances pixel par pixel


def _produits(uid):
    """Images de l'utilisateur, avec leur emprise (lue une fois dans l'archive)."""
    resultat = []
    for p in bd().execute("SELECT * FROM produit WHERE utilisateur_id = ? ORDER BY date_acquisition",
                          (uid,)).fetchall():
        try:
            emprise = emprise_du_produit(p)
        except Exception:
            continue                                  # archive illisible : ignorée
        resultat.append({"id": p["id"], "date": p["date_acquisition"], "tuile": p["tuile"],
                         "annee": int(p["date_acquisition"][:4]), "mois": int(p["date_acquisition"][5:7]),
                         "emprise": [round(v, 6) for v in emprise]})
    return resultat


def choisir_images(produits, zone, mois, debut, fin, uid):
    """Une image par année : celles du mois demandé, sur la tuile qui couvre
    le mieux la zone. Préférence à une image déjà analysée sur cette zone."""
    candidats = [p for p in produits if p["mois"] == mois and debut <= p["annee"] <= fin
                 and geometrie.intersection(zone, p["emprise"])]
    if not candidats:
        return None, None, []
    surface = lambda p: geometrie.surface_km2(*geometrie.intersection(zone, p["emprise"]))
    tuile = max(Counter(p["tuile"] for p in candidats).items(),
                key=lambda t: (t[1], max(surface(p) for p in candidats if p["tuile"] == t[0])))[0]
    candidats = [p for p in candidats if p["tuile"] == tuile]
    retenue = geometrie.intersection(zone, candidats[0]["emprise"])
    zone_texte = ",".join(f"{v:.6f}" for v in retenue)
    par_annee = {}
    for p in candidats:
        deja = bd().execute("SELECT 1 FROM analyse WHERE produit_id = ? AND zone = ? AND statut = 'terminee'",
                            (p["id"], zone_texte)).fetchone()
        rang = (0 if deja else 1, p["date"])
        if p["annee"] not in par_annee or rang < par_annee[p["annee"]][0]:
            par_annee[p["annee"]] = (rang, p)
    choisis = [par_annee[a][1] for a in sorted(par_annee)]
    return zone_texte, tuile, choisis


def detection_ou_404(detection_id):
    ligne = bd().execute("SELECT * FROM detection WHERE id = ? AND utilisateur_id = ?",
                         (detection_id, g.utilisateur["id"])).fetchone()
    if ligne is None:
        abort(404)
    return ligne


@bp.route("/detection")
@connexion_requise
def page():
    uid = g.utilisateur["id"]
    produits = _produits(uid)
    mois_dispo = Counter(p["mois"] for p in produits)
    annees = sorted({p["annee"] for p in produits})
    if produits:
        o = min(p["emprise"][0] for p in produits); s = min(p["emprise"][1] for p in produits)
        e = max(p["emprise"][2] for p in produits); n = max(p["emprise"][3] for p in produits)
        emprise = [o, s, e, n]
    else:
        emprise = None
    detections = bd().execute("SELECT * FROM detection WHERE utilisateur_id = ? ORDER BY id DESC",
                              (uid,)).fetchall()
    return render_template(
        "detection.html", produits=produits, emprise=emprise, annees=annees, mois=MOIS,
        mois_dispo=mois_dispo, mois_defaut=mois_dispo.most_common(1)[0][0] if mois_dispo else 2,
        detections=detections, min_images=MIN_IMAGES, conseil_images=CONSEIL_IMAGES)


@bp.route("/detection/lancer", methods=["POST"])
@connexion_requise
def lancer():
    retour = redirect(url_for("detection.page"))
    zone = geometrie.lire_zone(request.form.get("zone", ""))
    try:
        mois = int(request.form["mois"])
        debut, fin = int(request.form["annee_debut"]), int(request.form["annee_fin"])
    except (KeyError, ValueError):
        abort(400)
    if zone is None:
        flash("Choisissez d'abord une zone sur la carte.", "warning")
        return retour
    if debut > fin:
        debut, fin = fin, debut
    zone_texte, tuile, choisis = choisir_images(_produits(g.utilisateur["id"]), zone, mois, debut, fin,
                                                g.utilisateur["id"])
    if len(choisis) < MIN_IMAGES:
        flash(f"Il faut au moins {MIN_IMAGES} images de {MOIS[mois - 1]} sur cette zone entre {debut} et {fin} "
              f"(trouvées : {len(choisis)}). Importez-en d'autres dans « Images satellitaires ».", "danger")
        return retour
    surface = geometrie.surface_km2(*[float(v) for v in zone_texte.split(",")])
    if surface > config.SURFACE_MAX_KM2:
        flash(f"Zone trop grande : {surface:.0f} km² pour un maximum de {config.SURFACE_MAX_KM2} km².", "danger")
        return retour
    curseur = bd().execute(
        "INSERT INTO detection (utilisateur_id, zone, mois, annee_debut, annee_fin, produits) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (g.utilisateur["id"], zone_texte, mois, debut, fin, ",".join(str(p["id"]) for p in choisis)))
    bd().commit()
    taches.ajouter_detection(curseur.lastrowid)
    return redirect(url_for("detection.resultat", detection_id=curseur.lastrowid))


@bp.route("/detection/<int:detection_id>")
@connexion_requise
def resultat(detection_id):
    from moteur.config_modele import CLASSES, COULEURS
    d = detection_ou_404(detection_id)
    numeros = [int(v) for v in d["produits"].split(",")]
    marques = ",".join("?" * len(numeros))
    images = bd().execute(f"SELECT id, date_acquisition FROM produit WHERE id IN ({marques}) "
                          "ORDER BY date_acquisition", numeros).fetchall()
    zone = [float(v) for v in d["zone"].split(",")]
    contexte = {"d": d, "images": images, "zone": zone, "surface": geometrie.surface_km2(*zone),
                "mois": MOIS[d["mois"] - 1], "suivi": None}
    if d["statut"] == "terminee" and d["suivi_id"]:
        suivi = bd().execute("SELECT * FROM suivi WHERE id = ?", (d["suivi_id"],)).fetchone()
        resume = json.loads(suivi["resume"]) if suivi and suivi["resume"] else None
        if resume:
            annees = [x[:4] for x in resume["dates"]]
            terre = [1, 2, 3, 4]
            contexte.update(
                suivi=suivi, resume=resume, reponse=reponse_suivi(resume), zones=zones_du_suivi(suivi),
                libelles_avis=AVIS,
                derniere_analyse=int(d["analyses"].split(",")[-1]) if d["analyses"] else None,
                graphe_surfaces=graphiques.courbes(
                    [{"nom": CLASSES[c], "couleur": COULEURS[c],
                      "valeurs": [round(l["hectares"][c] / 100, 1) for l in resume["serie_terre"]]}
                     for c in terre]
                    + [{"nom": "Terres nues (sol nu + sable)", "couleur": "#b98b4e", "pointille": True,
                        "valeurs": [round(l["hectares"][5] / 100, 1) for l in resume["serie_terre"]]}],
                    annees, unite="km²", decimales=1, largeur=760),
                legende_surfaces=[(CLASSES[c], COULEURS[c]) for c in terre]
                                  + [("Terres nues (sol nu + sable)", "#b98b4e")],
                graphe_ndvi=graphiques.courbes(
                    [{"nom": "NDVI médian", "couleur": "#1c8457", "valeurs": resume["ndvi"]["medianes"]}],
                    annees, decimales=3, depuis_zero=False, aire=True, largeur=520)
                if resume.get("ndvi") else None)
    return render_template("detection_resultat.html", **contexte)


@bp.route("/detection/<int:detection_id>/etat")
@connexion_requise
def etat(detection_id):
    d = detection_ou_404(detection_id)
    return jsonify(statut=d["statut"], progression=d["progression"], message=d["message"], devant=0)


@bp.route("/detection/<int:detection_id>/supprimer", methods=["POST"])
@connexion_requise
def supprimer(detection_id):
    d = detection_ou_404(detection_id)
    if d["statut"] in ("en_attente", "en_cours"):
        flash("Cette détection est en cours : attendez la fin.", "warning")
        return redirect(url_for("detection.resultat", detection_id=detection_id))
    bd().execute("DELETE FROM detection WHERE id = ?", (detection_id,))
    bd().commit()
    flash("Détection retirée de la liste (les analyses et le suivi sont conservés).", "success")
    return redirect(url_for("detection.page"))
