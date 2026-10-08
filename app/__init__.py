"""Création de l'application Flask."""
import secrets

from flask import Flask, abort, g, request, session

import config
from app import modeles_bd


def creer_app(surcharge=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=config.CLE_SECRETE,
        MAX_CONTENT_LENGTH=config.TAILLE_MAX_ENVOI,
        CHEMIN_BASE=str(config.CHEMIN_BASE),
        DOSSIER_IMPORTS=str(config.DOSSIER_IMPORTS),
        DOSSIER_RESULTATS=str(config.DOSSIER_RESULTATS),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if surcharge:
        app.config.update(surcharge)

    modeles_bd.initialiser(app)

    # --- Utilisateur connecté, chargé à chaque requête ---
    @app.before_request
    def charger_utilisateur():
        g.utilisateur = None
        identifiant = session.get("utilisateur_id")
        if identifiant is not None:
            g.utilisateur = modeles_bd.bd().execute(
                "SELECT id, nom, email FROM utilisateur WHERE id = ?",
                (identifiant,)).fetchone()
            if g.utilisateur is None:
                session.clear()

    # --- Protection des formulaires (jeton anti-CSRF) ---
    def jeton():
        if "jeton" not in session:
            session["jeton"] = secrets.token_hex(16)
        return session["jeton"]

    @app.before_request
    def verifier_jeton():
        if request.method == "POST":
            recu = request.form.get("jeton") or request.headers.get("X-Jeton")
            if not recu or recu != session.get("jeton"):
                abort(400, "Formulaire expiré : rechargez la page.")

    app.jinja_env.globals["jeton"] = jeton

    # --- Données du modèle disponibles dans toutes les pages ---
    from moteur import config_modele as cm
    app.jinja_env.globals["classes_modele"] = list(zip(cm.CLASSES, cm.COULEURS))
    app.jinja_env.globals["emprise_modele"] = list(cm.EMPRISE_ENTRAINEMENT)
    app.jinja_env.globals["tuiles_modele"] = sorted(cm.TUILES_ENTRAINEMENT)
    app.jinja_env.filters["zip"] = lambda a, b: list(zip(a, b))
    app.jinja_env.globals["nom_systeme"] = config.NOM_SYSTEME
    app.jinja_env.globals["sous_titre"] = config.SOUS_TITRE
    app.jinja_env.globals["surface_max"] = config.SURFACE_MAX_KM2

    from app.routes import analyses, auth, main, suivi
    app.register_blueprint(main.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(analyses.bp)
    app.register_blueprint(suivi.bp)

    if app.config.get("LANCER_EXECUTANT", True):
        from app import taches
        taches.demarrer(app)
    return app
