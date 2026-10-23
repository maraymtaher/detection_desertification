"""Création de l'application Flask."""
import json
import re
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
        PERMANENT_SESSION_LIFETIME=30 * 24 * 3600,
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
    app.jinja_env.filters["fromjson"] = json.loads

    def format_fr(valeur, decimales=1):
        """Nombre au format français : 268 289 ; 69,8."""
        if valeur is None:
            return "—"
        texte = f"{float(valeur):,.{int(decimales)}f}"
        return texte.replace(",", "\u202f").replace(".", ",")
    app.jinja_env.filters["fr"] = format_fr
    # Texte déjà enregistré (« 69.8 % ») : virgule décimale
    app.jinja_env.filters["virgule"] = lambda texte: re.sub(r"(\d)\.(\d)", r"\1,\2", str(texte))
    app.jinja_env.globals["nom_systeme"] = config.NOM_SYSTEME
    app.jinja_env.globals["sous_titre"] = config.SOUS_TITRE
    app.jinja_env.globals["surface_max"] = config.SURFACE_MAX_KM2
    from moteur.serie import VERSION as VERSION_METHODE
    app.jinja_env.globals["version_methode"] = VERSION_METHODE

    from moteur import tracabilite

    @app.context_processor
    def version_modele():
        """Version du système et empreinte du modèle, pour le pied de page."""
        return {"version_systeme": tracabilite.VERSION_SYSTEME,
                "modele_courant": tracabilite.empreinte_modele()}

    @app.context_processor
    def alertes():
        """Nombre de zones à surveiller du dernier suivi sans avis (cloche)."""
        if g.get("utilisateur") is None:
            return {"alertes_a_verifier": 0}
        from app.indicateurs import zones_du_suivi
        dernier = modeles_bd.bd().execute(
            "SELECT * FROM suivi WHERE utilisateur_id = ? AND statut = 'terminee' "
            "ORDER BY id DESC LIMIT 1", (g.utilisateur["id"],)).fetchone()
        z = zones_du_suivi(dernier) if dernier else None
        return {"alertes_a_verifier": (z["total"] - z["verifies"]) if z else 0}

    from app.routes import (analyses, auth, detection, historique, images, main, qualite, vues,
                            rapports, statistiques, suivi, zones)
    for module in (detection, images, statistiques, historique, vues):
        app.register_blueprint(module.bp)
    app.register_blueprint(qualite.bp)
    app.register_blueprint(rapports.bp)
    app.register_blueprint(main.bp)
    app.register_blueprint(auth.bp)
    app.register_blueprint(analyses.bp)
    app.register_blueprint(suivi.bp)
    app.register_blueprint(zones.bp)

    if app.config.get("LANCER_EXECUTANT", True):
        from app import taches
        taches.demarrer(app)
    return app
