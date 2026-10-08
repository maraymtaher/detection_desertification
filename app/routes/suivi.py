"""Suivi temporel : comparaison de plusieurs analyses de la même zone."""
import json
import shutil

from flask import (Blueprint, abort, flash, g, jsonify, redirect,
                   render_template, request, send_from_directory, url_for)

from app import geometrie, taches
from app.modeles_bd import bd
from app.routes.auth import connexion_requise

FICHIERS = {"changement.png", "changement.tif", "serie.csv", "transitions.csv"}

bp = Blueprint("suivi", __name__)


def suivi_ou_404(suivi_id):
    ligne = bd().execute(
        "SELECT * FROM suivi WHERE id = ? AND utilisateur_id = ?",
        (suivi_id, g.utilisateur["id"])).fetchone()
    if ligne is None:
        abort(404)
    return ligne


def groupes_comparables():
    """Analyses terminées, regroupées par tuile et zone identiques :
    seules des cartes de la même zone peuvent être comparées."""
    lignes = bd().execute(
        "SELECT a.id, a.zone, p.tuile, p.date_acquisition FROM analyse a "
        "JOIN produit p ON p.id = a.produit_id "
        "WHERE a.utilisateur_id = ? AND a.statut = 'terminee' "
        "ORDER BY p.date_acquisition, a.id", (g.utilisateur["id"],)).fetchall()
    groupes = {}
    for l in lignes:
        groupes.setdefault((l["tuile"], l["zone"]), []).append(l)
    resultat = []
    for (tuile, zone), analyses in groupes.items():
        resultat.append({
            "tuile": tuile, "zone": zone, "analyses": analyses,
            "surface": geometrie.surface_km2(*[float(v) for v in zone.split(",")]),
            "nb_dates": len({a["date_acquisition"] for a in analyses}),
        })
    resultat.sort(key=lambda gr: -gr["nb_dates"])
    return resultat


@bp.route("/suivi")
@connexion_requise
def liste():
    suivis = bd().execute(
        "SELECT * FROM suivi WHERE utilisateur_id = ? ORDER BY id DESC",
        (g.utilisateur["id"],)).fetchall()
    return render_template("suivis.html", groupes=groupes_comparables(), suivis=suivis)


@bp.route("/suivi/creer", methods=["POST"])
@connexion_requise
def creer():
    retour = redirect(url_for("suivi.liste"))
    try:
        numeros = sorted({int(v) for v in request.form.getlist("analyse")})
    except ValueError:
        abort(400)
    if len(numeros) < 2:
        flash("Cochez au moins deux analyses.", "warning")
        return retour
    marques = ",".join("?" * len(numeros))
    analyses = bd().execute(
        "SELECT a.id, a.zone, a.statut, p.tuile, p.date_acquisition FROM analyse a "
        f"JOIN produit p ON p.id = a.produit_id WHERE a.id IN ({marques}) "
        "AND a.utilisateur_id = ?", (*numeros, g.utilisateur["id"])).fetchall()
    if len(analyses) != len(numeros) or any(a["statut"] != "terminee" for a in analyses):
        flash("Certaines analyses sont introuvables ou non terminées.", "danger")
        return retour
    if len({(a["tuile"], a["zone"]) for a in analyses}) != 1:
        flash("Les analyses doivent porter sur la même tuile et la même zone.", "danger")
        return retour
    if len({a["date_acquisition"] for a in analyses}) != len(analyses):
        flash("Deux analyses cochées ont la même date : gardez-en une seule par date.",
              "warning")
        return retour
    curseur = bd().execute(
        "INSERT INTO suivi (utilisateur_id, analyses, tuile, zone) VALUES (?, ?, ?, ?)",
        (g.utilisateur["id"], ",".join(str(n) for n in numeros),
         analyses[0]["tuile"], analyses[0]["zone"]))
    bd().commit()
    taches.ajouter_suivi(curseur.lastrowid)
    return redirect(url_for("suivi.detail", suivi_id=curseur.lastrowid))


@bp.route("/suivi/<int:suivi_id>")
@connexion_requise
def detail(suivi_id):
    suivi = suivi_ou_404(suivi_id)
    resume = json.loads(suivi["resume"]) if suivi["resume"] else None
    return render_template(
        "suivi.html", suivi=suivi, resume=resume,
        surface=geometrie.surface_km2(*[float(v) for v in suivi["zone"].split(",")]))


@bp.route("/suivi/<int:suivi_id>/etat")
@connexion_requise
def etat(suivi_id):
    suivi = suivi_ou_404(suivi_id)
    return jsonify(statut=suivi["statut"], progression=suivi["progression"],
                   message=suivi["message"], devant=0)


@bp.route("/suivi/<int:suivi_id>/fichiers/<nom>")
@connexion_requise
def fichier(suivi_id, nom):
    suivi = suivi_ou_404(suivi_id)
    if nom not in FICHIERS or suivi["statut"] != "terminee":
        abort(404)
    return send_from_directory(suivi["dossier"], nom,
                               as_attachment=nom.endswith((".tif", ".csv")))


@bp.route("/suivi/<int:suivi_id>/supprimer", methods=["POST"])
@connexion_requise
def supprimer(suivi_id):
    suivi = suivi_ou_404(suivi_id)
    if suivi["statut"] == "en_cours":
        flash("Ce suivi est en cours de calcul : attendez la fin.", "warning")
        return redirect(url_for("suivi.detail", suivi_id=suivi_id))
    if suivi["dossier"]:
        shutil.rmtree(suivi["dossier"], ignore_errors=True)
    bd().execute("DELETE FROM suivi WHERE id = ?", (suivi_id,))
    bd().commit()
    flash("Suivi supprimé.", "success")
    return redirect(url_for("suivi.liste"))
