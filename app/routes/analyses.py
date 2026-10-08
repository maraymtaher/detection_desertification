"""Produits Sentinel-2 et analyses : import, zone, lancement, suivi."""
import json
import os
import shutil
import uuid
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect,
                   render_template, request, send_from_directory, url_for)

import config
from app import geometrie, taches
from app.modeles_bd import bd
from app.routes.auth import connexion_requise
from moteur.config_modele import EMPRISE_ENTRAINEMENT, LIBELLES_SAISONS, saison
from moteur.lecture_safe import lire_produit

# Fichiers de résultats qu'un utilisateur peut consulter ou télécharger
FICHIERS_RESULTATS = {"carte.png", "controle.png", "classes.tif", "statistiques.csv"}

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
    analyses = bd().execute(
        "SELECT * FROM analyse WHERE produit_id = ? ORDER BY id DESC",
        (produit_id,)).fetchall()
    return render_template(
        "produit.html", produit=produit, analyses=analyses,
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


# ---------------------------------------------------------------------
# Analyses : choix de la zone, lancement, suivi, résultats
# ---------------------------------------------------------------------
def emprise_du_produit(produit):
    """Emprise (ouest, sud, est, nord) du produit, calculée une fois puis
    conservée dans la base."""
    if produit["emprise"]:
        return tuple(float(v) for v in produit["emprise"].split(","))
    from moteur import analyse as moteur_analyse
    emprise = moteur_analyse.emprise_produit(produit["chemin"])
    bd().execute("UPDATE produit SET emprise = ? WHERE id = ?",
                 (",".join(f"{v:.6f}" for v in emprise), produit["id"]))
    bd().commit()
    return emprise


def analyse_ou_404(analyse_id):
    ligne = bd().execute(
        "SELECT a.*, p.date_acquisition, p.tuile, p.nom AS nom_produit "
        "FROM analyse a JOIN produit p ON p.id = a.produit_id "
        "WHERE a.id = ? AND a.utilisateur_id = ?",
        (analyse_id, g.utilisateur["id"])).fetchone()
    if ligne is None:
        abort(404)
    return ligne


@bp.route("/produits/<int:produit_id>/zone")
@connexion_requise
def zone(produit_id):
    produit = produit_ou_404(produit_id)
    try:
        emprise = emprise_du_produit(produit)
    except Exception as e:
        flash(f"Impossible de lire l'emprise de l'image : {e}", "danger")
        return redirect(url_for("analyses.fiche", produit_id=produit_id))
    return render_template("zone.html", produit=produit, emprise=list(emprise))


@bp.route("/produits/<int:produit_id>/analyser", methods=["POST"])
@connexion_requise
def analyser(produit_id):
    produit = produit_ou_404(produit_id)
    retour = redirect(url_for("analyses.zone", produit_id=produit_id))
    demandee = geometrie.lire_zone(request.form.get("zone", ""))
    if demandee is None:
        flash("Dessinez d'abord une zone sur la carte.", "warning")
        return retour
    retenue = geometrie.intersection(demandee, emprise_du_produit(produit))
    if retenue is None:
        flash("La zone choisie est en dehors de l'image.", "danger")
        return retour
    surface = geometrie.surface_km2(*retenue)
    if surface > config.SURFACE_MAX_KM2:
        flash(f"Zone trop grande : {surface:.0f} km² pour un maximum de "
              f"{config.SURFACE_MAX_KM2} km².", "danger")
        return retour
    curseur = bd().execute(
        "INSERT INTO analyse (utilisateur_id, produit_id, zone) VALUES (?, ?, ?)",
        (g.utilisateur["id"], produit_id, ",".join(f"{v:.6f}" for v in retenue)))
    bd().commit()
    taches.ajouter(curseur.lastrowid)
    return redirect(url_for("analyses.detail", analyse_id=curseur.lastrowid))


@bp.route("/analyses")
@connexion_requise
def liste():
    analyses = bd().execute(
        "SELECT a.*, p.date_acquisition, p.tuile FROM analyse a "
        "JOIN produit p ON p.id = a.produit_id "
        "WHERE a.utilisateur_id = ? ORDER BY a.id DESC",
        (g.utilisateur["id"],)).fetchall()
    return render_template("analyses.html", analyses=analyses)


@bp.route("/analyses/<int:analyse_id>")
@connexion_requise
def detail(analyse_id):
    analyse = analyse_ou_404(analyse_id)
    resume = json.loads(analyse["resume"]) if analyse["resume"] else None
    return render_template(
        "analyse.html", analyse=analyse, resume=resume,
        zone=[float(v) for v in analyse["zone"].split(",")],
        surface=geometrie.surface_km2(*[float(v) for v in analyse["zone"].split(",")]))


@bp.route("/analyses/<int:analyse_id>/etat")
@connexion_requise
def etat(analyse_id):
    analyse = analyse_ou_404(analyse_id)
    devant = 0
    if analyse["statut"] == "en_attente":
        devant = bd().execute(
            "SELECT COUNT(*) FROM analyse WHERE id < ? AND statut IN "
            "('en_attente', 'en_cours')", (analyse_id,)).fetchone()[0]
    return jsonify(statut=analyse["statut"], progression=analyse["progression"],
                   message=analyse["message"], devant=devant)


@bp.route("/analyses/<int:analyse_id>/fichiers/<nom>")
@connexion_requise
def fichier(analyse_id, nom):
    analyse = analyse_ou_404(analyse_id)
    if nom not in FICHIERS_RESULTATS or analyse["statut"] != "terminee":
        abort(404)
    return send_from_directory(analyse["dossier"], nom,
                               as_attachment=nom.endswith((".tif", ".csv")))


@bp.route("/analyses/<int:analyse_id>/supprimer", methods=["POST"])
@connexion_requise
def supprimer_analyse(analyse_id):
    analyse = analyse_ou_404(analyse_id)
    if analyse["statut"] == "en_cours":
        flash("Cette analyse est en cours : attendez la fin avant de la supprimer.",
              "warning")
        return redirect(url_for("analyses.detail", analyse_id=analyse_id))
    for s in bd().execute("SELECT id, analyses FROM suivi WHERE utilisateur_id = ?",
                          (g.utilisateur["id"],)):
        if str(analyse_id) in s["analyses"].split(","):
            flash(f"Cette analyse est utilisée par le suivi n° {s['id']} : "
                  "supprimez d'abord ce suivi.", "warning")
            return redirect(url_for("analyses.detail", analyse_id=analyse_id))
    if analyse["dossier"]:
        shutil.rmtree(analyse["dossier"], ignore_errors=True)
    bd().execute("DELETE FROM analyse WHERE id = ?", (analyse_id,))
    bd().commit()
    flash("Analyse supprimée.", "success")
    return redirect(url_for("analyses.liste"))
