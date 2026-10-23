"""Rapports PDF et exports : une page qui rassemble tout ce qui peut être téléchargé."""
import json

from flask import Blueprint, Response, abort, g, render_template

import config
from app import geometrie
from app.indicateurs import VERSION_METHODE, reponse_suivi, zones_du_suivi
from app.modeles_bd import bd
from app.routes.auth import connexion_requise
from app.routes.suivi import suivi_ou_404

bp = Blueprint("rapports", __name__)


@bp.route("/rapports")
@connexion_requise
def liste():
    uid = g.utilisateur["id"]
    suivis = []
    for s in bd().execute("SELECT * FROM suivi WHERE utilisateur_id = ? AND statut = 'terminee' "
                          "ORDER BY id DESC", (uid,)).fetchall():
        resume = json.loads(s["resume"])
        numeros = [int(v) for v in s["analyses"].split(",")]
        derniere = bd().execute(
            "SELECT a.id FROM analyse a JOIN produit p ON p.id = a.produit_id WHERE a.id IN "
            f"({','.join('?' * len(numeros))}) ORDER BY p.date_acquisition DESC LIMIT 1", numeros).fetchone()
        suivis.append({"ligne": s, "resume": resume, "reponse": reponse_suivi(resume),
                       "derniere": derniere["id"] if derniere else None,
                       "zones": zones_du_suivi(s),
                       "surface": geometrie.surface_km2(*[float(v) for v in s["zone"].split(",")])})
    analyses = bd().execute(
        "SELECT a.*, p.date_acquisition, p.tuile FROM analyse a JOIN produit p ON p.id = a.produit_id "
        "WHERE a.utilisateur_id = ? AND a.statut = 'terminee' ORDER BY p.date_acquisition DESC, a.id DESC",
        (uid,)).fetchall()
    sans_empreinte = bd().execute(
        "SELECT COUNT(*) FROM produit WHERE utilisateur_id = ? AND sha256 = ''", (uid,)).fetchone()[0]
    return render_template("rapports.html", suivis=suivis, analyses=analyses,
                           valides=[x for x in suivis if x["resume"].get("version") == VERSION_METHODE],
                           sans_empreinte=sans_empreinte)


def _pdf(contenu, nom):
    return Response(contenu, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nom}"'})


@bp.route("/rapports/suivi/<int:suivi_id>.pdf")
@connexion_requise
def pdf_suivi(suivi_id):
    from app.rapport_pdf import rapport_suivi
    suivi = suivi_ou_404(suivi_id)
    if suivi["statut"] != "terminee" or json.loads(suivi["resume"]).get("version") != VERSION_METHODE:
        abort(404)
    return _pdf(rapport_suivi(suivi, g.utilisateur, config.NOM_SYSTEME),
                f"rapport_suivi_{suivi_id}.pdf")


@bp.route("/rapports/analyse/<int:analyse_id>.pdf")
@connexion_requise
def pdf_analyse(analyse_id):
    from app.rapport_pdf import rapport_analyse
    from app.routes.analyses import analyse_ou_404
    analyse = analyse_ou_404(analyse_id)
    if analyse["statut"] != "terminee":
        abort(404)
    return _pdf(rapport_analyse(analyse, g.utilisateur, config.NOM_SYSTEME),
                f"rapport_analyse_{analyse_id}.pdf")
