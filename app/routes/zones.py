"""Zones à surveiller : foyers de dégradation d'un suivi et vérification humaine."""
import json
import re
from pathlib import Path

from flask import (Blueprint, abort, flash, g, jsonify, redirect,
                   render_template, request, send_from_directory, url_for)

from app import taches
from app.modeles_bd import bd
from app.indicateurs import VERSION_METHODE
from app.routes.auth import connexion_requise
from app.routes.suivi import suivi_ou_404

bp = Blueprint("zones", __name__)

AVIS = {
    "visible": "Dégradation visible sur les images",
    "non_visible": "Pas de dégradation visible",
    "incertain": "Incertain",
}
FICHIER_AUTORISE = re.compile(r"^(foyers\.(kml|geojson)|foyer_\d{2}_(avant|apres|contour)\.png)$")


def _resultat(suivi):
    chemin = Path(suivi["dossier"]) / "foyers" / "foyers.json"
    if not suivi["dossier"] or not chemin.exists():
        return None
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)


def _etat(suivi_id):
    return bd().execute("SELECT * FROM zones WHERE suivi_id = ?", (suivi_id,)).fetchone()


def _avis(suivi_id):
    return {a["numero"]: a for a in bd().execute(
        "SELECT * FROM avis WHERE suivi_id = ?", (suivi_id,))}


def bilan_avis(avis, nb_foyers):
    comptes = {cle: sum(1 for a in avis.values() if a["avis"] == cle) for cle in AVIS}
    return {"comptes": comptes, "verifies": sum(comptes.values()), "total": nb_foyers}


@bp.route("/zones")
@connexion_requise
def liste():
    suivis = bd().execute(
        "SELECT s.*, z.statut AS statut_zones FROM suivi s "
        "LEFT JOIN zones z ON z.suivi_id = s.id "
        "WHERE s.utilisateur_id = ? AND s.statut = 'terminee' ORDER BY s.id DESC",
        (g.utilisateur["id"],)).fetchall()
    if len(suivis) == 1:
        return redirect(url_for("zones.detail", suivi_id=suivis[0]["id"]))
    return render_template("zones.html", suivis=suivis)


@bp.route("/zones/<int:suivi_id>")
@connexion_requise
def detail(suivi_id):
    suivi = suivi_ou_404(suivi_id)
    resume_suivi = json.loads(suivi["resume"]) if suivi["resume"] else None
    etat = _etat(suivi_id)
    resultat = _resultat(suivi) if etat and etat["statut"] == "terminee" else None
    avis = _avis(suivi_id)
    return render_template(
        "zones_suivi.html", suivi=suivi, resume_suivi=resume_suivi, etat=etat,
        resultat=resultat, avis=avis, libelles_avis=AVIS,
        bilan=bilan_avis(avis, resultat["nb_foyers"]) if resultat else None)


@bp.route("/zones/<int:suivi_id>/calculer", methods=["POST"])
@connexion_requise
def calculer(suivi_id):
    suivi = suivi_ou_404(suivi_id)
    resume_suivi = json.loads(suivi["resume"]) if suivi["resume"] else {}
    if suivi["statut"] != "terminee" or resume_suivi.get("version") != VERSION_METHODE:
        flash("Recalculez d'abord le suivi avec la méthode actuelle.", "warning")
        return redirect(url_for("zones.detail", suivi_id=suivi_id))
    etat = _etat(suivi_id)
    if etat and etat["statut"] in ("en_attente", "en_cours"):
        return redirect(url_for("zones.detail", suivi_id=suivi_id))
    bd().execute("DELETE FROM avis WHERE suivi_id = ?", (suivi_id,))
    bd().execute("DELETE FROM zones WHERE suivi_id = ?", (suivi_id,))
    bd().execute("INSERT INTO zones (suivi_id) VALUES (?)", (suivi_id,))
    bd().commit()
    taches.ajouter_foyers(suivi_id)
    return redirect(url_for("zones.detail", suivi_id=suivi_id))


@bp.route("/zones/<int:suivi_id>/etat")
@connexion_requise
def etat(suivi_id):
    suivi_ou_404(suivi_id)
    e = _etat(suivi_id)
    if e is None:
        abort(404)
    return jsonify(statut=e["statut"], progression=e["progression"], message=e["message"], devant=0)


@bp.route("/zones/<int:suivi_id>/foyer/<int:numero>", methods=["GET", "POST"])
@connexion_requise
def fiche(suivi_id, numero):
    suivi = suivi_ou_404(suivi_id)
    resultat = _resultat(suivi)
    if resultat is None or not 1 <= numero <= resultat["nb_foyers"]:
        abort(404)
    if request.method == "POST":
        valeur = request.form.get("avis", "")
        if valeur not in AVIS:
            flash("Choisissez un avis.", "warning")
        else:
            commentaire = request.form.get("commentaire", "").strip()[:500]
            bd().execute(
                "INSERT INTO avis (suivi_id, numero, utilisateur_id, avis, commentaire) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (suivi_id, numero) DO UPDATE SET "
                "avis = excluded.avis, commentaire = excluded.commentaire, "
                "utilisateur_id = excluded.utilisateur_id, modifie_le = CURRENT_TIMESTAMP",
                (suivi_id, numero, g.utilisateur["id"], valeur, commentaire))
            bd().commit()
            flash(f"Avis enregistré pour la zone {numero}.", "success")
            suivant = numero + 1 if numero < resultat["nb_foyers"] else None
            if suivant:
                return redirect(url_for("zones.fiche", suivi_id=suivi_id, numero=suivant))
            return redirect(url_for("zones.detail", suivi_id=suivi_id))
    foyer = resultat["foyers"][numero - 1]
    return render_template(
        "zone_fiche.html", suivi=suivi, resultat=resultat, foyer=foyer,
        avis=_avis(suivi_id).get(numero), libelles_avis=AVIS)


@bp.route("/zones/<int:suivi_id>/fichiers/<nom>")
@connexion_requise
def fichier(suivi_id, nom):
    suivi = suivi_ou_404(suivi_id)
    if not FICHIER_AUTORISE.match(nom) or not suivi["dossier"]:
        abort(404)
    return send_from_directory(Path(suivi["dossier"]) / "foyers", nom,
                               as_attachment=not nom.endswith(".png"))
