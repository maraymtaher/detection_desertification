"""Produits Sentinel-2 : import, vérification, fiche, suppression."""
import os
import uuid
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect,
                   render_template, request, url_for)

from app.modeles_bd import bd
from app.routes.auth import connexion_requise
from moteur.config_modele import LIBELLES_SAISONS, saison
from moteur.lecture_safe import lire_produit

bp = Blueprint("analyses", __name__)


def produit_ou_404(produit_id):
    """Le produit demandé, seulement s'il appartient à l'utilisateur."""
    ligne = bd().execute(
        "SELECT * FROM produit WHERE id = ? AND utilisateur_id = ?",
        (produit_id, g.utilisateur["id"])).fetchone()
    if ligne is None:
        abort(404)
    return ligne


def _reponse(ok, message, url=None, code=200):
    """Réponse JSON pour l'envoi avec barre de progression,
    ou redirection classique sinon."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify(ok=ok, message=message, url=url), code
    flash(message, "success" if ok else "danger")
    return redirect(url or url_for("analyses.importer"))


@bp.route("/produits/importer", methods=["GET", "POST"])
@connexion_requise
def importer():
    if request.method == "GET":
        return render_template("importer.html")

    fichier = request.files.get("archive")
    if fichier is None or not fichier.filename:
        return _reponse(False, "Aucun fichier reçu.", code=400)
    if not fichier.filename.lower().endswith(".zip"):
        return _reponse(False, "Seules les archives .zip sont acceptées.", code=400)

    dossier = Path(current_app.config["DOSSIER_IMPORTS"]) / str(g.utilisateur["id"])
    dossier.mkdir(parents=True, exist_ok=True)
    temporaire = dossier / f"envoi_{uuid.uuid4().hex}.zip"
    fichier.save(temporaire)

    produit = lire_produit(temporaire)
    if not produit.valide:
        temporaire.unlink(missing_ok=True)
        return _reponse(False, "Produit refusé : " + " ; ".join(produit.erreurs),
                        code=400)

    existant = bd().execute(
        "SELECT id FROM produit WHERE utilisateur_id = ? AND nom = ?",
        (g.utilisateur["id"], produit.nom)).fetchone()
    if existant:
        temporaire.unlink(missing_ok=True)
        return _reponse(True, "Ce produit était déjà importé : il est réutilisé.",
                        url_for("analyses.fiche", produit_id=existant["id"]))

    definitif = dossier / f"{produit.nom}.zip"
    os.replace(temporaire, definitif)
    curseur = bd().execute(
        "INSERT INTO produit (utilisateur_id, nom, satellite, tuile, "
        "date_acquisition, version_traitement, chemin, avertissements) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (g.utilisateur["id"], produit.nom, produit.satellite, produit.tuile,
         produit.date, produit.version_traitement, str(definitif),
         "\n".join(produit.avertissements)))
    bd().commit()
    return _reponse(True, "Produit importé et vérifié.",
                    url_for("analyses.fiche", produit_id=curseur.lastrowid))


@bp.route("/produits/<int:produit_id>")
@connexion_requise
def fiche(produit_id):
    produit = produit_ou_404(produit_id)
    return render_template(
        "produit.html", produit=produit,
        saison=LIBELLES_SAISONS[saison(produit["date_acquisition"])],
        avertissements=[a for a in produit["avertissements"].split("\n") if a])


@bp.route("/produits/<int:produit_id>/supprimer", methods=["POST"])
@connexion_requise
def supprimer(produit_id):
    produit = produit_ou_404(produit_id)
    utilise = bd().execute(
        "SELECT COUNT(*) FROM analyse WHERE produit_id = ?",
        (produit_id,)).fetchone()[0]
    if utilise:
        flash("Ce produit est utilisé par des analyses : "
              "supprimez-les d'abord.", "warning")
        return redirect(url_for("analyses.fiche", produit_id=produit_id))
    Path(produit["chemin"]).unlink(missing_ok=True)
    bd().execute("DELETE FROM produit WHERE id = ?", (produit_id,))
    bd().commit()
    flash("Produit supprimé.", "success")
    return redirect(url_for("main.accueil"))
