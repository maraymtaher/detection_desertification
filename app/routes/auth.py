"""Inscription, connexion, déconnexion."""
import functools
import re
import sqlite3

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import config
from app.modeles_bd import bd

bp = Blueprint("auth", __name__)
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def connexion_requise(vue):
    """Redirige vers la page de connexion si personne n'est connecté."""
    @functools.wraps(vue)
    def enveloppe(*args, **kwargs):
        if g.utilisateur is None:
            flash("Connectez-vous pour accéder à cette page.", "warning")
            return redirect(url_for("auth.connexion"))
        return vue(*args, **kwargs)
    return enveloppe


@bp.route("/inscription", methods=["GET", "POST"])
def inscription():
    if request.method == "POST":
        nom = request.form.get("nom", "").strip()
        email = request.form.get("email", "").strip().lower()
        mdp = request.form.get("mot_de_passe", "")
        erreur = None
        if not nom:
            erreur = "Le nom est obligatoire."
        elif not EMAIL.match(email):
            erreur = "Adresse e-mail invalide."
        elif len(mdp) < config.LONGUEUR_MIN_MOT_DE_PASSE:
            erreur = (f"Le mot de passe doit contenir au moins "
                      f"{config.LONGUEUR_MIN_MOT_DE_PASSE} caractères.")
        elif mdp != request.form.get("confirmation", ""):
            erreur = "Les deux mots de passe ne sont pas identiques."
        elif request.form.get("accepte") != "1":
            erreur = "Cochez la case d'accord pour créer le compte."
        if erreur is None:
            try:
                profil = request.form.get("profil", "")[:40]
                bd().execute(
                    "INSERT INTO utilisateur (nom, email, mot_de_passe, profil) "
                    "VALUES (?, ?, ?, ?)",
                    (nom, email, generate_password_hash(mdp), profil))
                bd().commit()
            except sqlite3.IntegrityError:
                erreur = "Un compte existe déjà avec cette adresse."
        if erreur is None:
            flash("Compte créé. Vous pouvez vous connecter.", "success")
            return redirect(url_for("auth.connexion"))
        flash(erreur, "danger")
    return render_template("inscription.html")


@bp.route("/connexion", methods=["GET", "POST"])
def connexion():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        ligne = bd().execute(
            "SELECT id, mot_de_passe FROM utilisateur WHERE email = ?",
            (email,)).fetchone()
        if ligne and check_password_hash(ligne["mot_de_passe"],
                                         request.form.get("mot_de_passe", "")):
            session.clear()
            session["utilisateur_id"] = ligne["id"]
            # « Se souvenir de moi » : session gardée 30 jours, sinon fermée avec le navigateur
            session.permanent = request.form.get("souvenir") == "1"
            return redirect(url_for("auth.verification"))
        flash("Adresse e-mail ou mot de passe incorrect.", "danger")
    return render_template("connexion.html")


@bp.route("/deconnexion", methods=["POST"])
def deconnexion():
    session.clear()
    return redirect(url_for("main.accueil"))


@bp.route("/verification")
def verification():
    """Écran de transition après la connexion (maquette 5)."""
    if g.utilisateur is None:
        return redirect(url_for("auth.connexion"))
    uid = g.utilisateur["id"]
    return render_template(
        "verification.html",
        nb_images=bd().execute("SELECT COUNT(*) FROM produit WHERE utilisateur_id = ?", (uid,)).fetchone()[0],
        nb_analyses=bd().execute("SELECT COUNT(*) FROM analyse WHERE utilisateur_id = ? AND statut = 'terminee'",
                                 (uid,)).fetchone()[0])


@bp.route("/mot-de-passe-oublie", methods=["GET", "POST"])
def mot_de_passe_oublie():
    """Sans serveur d'e-mails (installation locale), aucun lien ne peut être
    envoyé : la page l'indique et donne la commande de réinitialisation."""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        if not EMAIL.match(email):
            flash("Adresse e-mail invalide.", "danger")
            return render_template("mot_de_passe.html", envoye=False)
        return render_template("mot_de_passe.html", envoye=True, email=email)
    return render_template("mot_de_passe.html", envoye=False)
