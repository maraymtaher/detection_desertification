"""Validation et qualité : performances mesurées du modèle, qualité de chaque
image analysée et bilan de la vérification humaine des zones à surveiller."""
import json
from pathlib import Path

from flask import Blueprint, g, render_template

from app.indicateurs import zones_du_suivi
from app.modeles_bd import bd
from app.routes.auth import connexion_requise

bp = Blueprint("qualite", __name__)

CHEMIN_EVALUATION = Path(__file__).resolve().parents[2] / "moteur" / "evaluation_modele.json"


def evaluation_modele():
    with open(CHEMIN_EVALUATION, encoding="utf-8") as f:
        return json.load(f)


@bp.route("/qualite")
@connexion_requise
def page():
    uid = g.utilisateur["id"]
    images = []
    for a in bd().execute(
            "SELECT a.id, a.resume, p.date_acquisition, p.tuile FROM analyse a "
            "JOIN produit p ON p.id = a.produit_id WHERE a.utilisateur_id = ? "
            "AND a.statut = 'terminee' ORDER BY p.date_acquisition, a.id", (uid,)):
        r = json.loads(a["resume"])
        images.append({"id": a["id"], "date": a["date_acquisition"], "tuile": a["tuile"],
                       "saison": r.get("saison", ""),
                       "non_classe": r["statistiques"]["part_non_classee"],
                       "hors": r.get("part_hors_entrainement", 0),
                       "tracee": bool((r.get("tracabilite") or {}).get("modele"))})
    pire = max(images, key=lambda i: i["non_classe"]) if images else None

    verifications = []
    for s in bd().execute("SELECT * FROM suivi WHERE utilisateur_id = ? AND statut = 'terminee' "
                          "ORDER BY id DESC", (uid,)).fetchall():
        z = zones_du_suivi(s)
        if z:
            verifications.append({"suivi": s, "zones": z})

    from moteur.tracabilite import empreinte_modele
    return render_template("qualite.html", evaluation=evaluation_modele(), images=images,
                           pire=pire, verifications=verifications, modele=empreinte_modele())
