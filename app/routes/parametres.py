"""Paramètres : compte de l'utilisateur, préférences d'affichage et
paramètres du système. Les paramètres scientifiques sont affichés en
lecture seule : ils sont liés au modèle entraîné et ne doivent pas être
modifiés depuis l'interface."""
from flask import Blueprint, current_app, flash, g, redirect, render_template, request, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import config
from app.modeles_bd import bd
from app.routes.auth import connexion_requise

bp = Blueprint("parametres", __name__)

# Formules telles qu'elles sont codées dans moteur/pretraitement.py
FORMULES = [
    ("NDVI", "(B8 − B4) / (B8 + B4)", "Végétation verte"),
    ("NDWI", "(B3 − B8) / (B3 + B8)", "Eau de surface"),
    ("BSI", "((B11 + B4) − (B8 + B2)) / ((B11 + B4) + (B8 + B2))", "Sol nu"),
    ("NDRE", "(B8A − B5) / (B8A + B5)", "Bord rouge (état de la végétation)"),
]
LIBELLES_SCL = {0: "sans donnée", 3: "ombre de nuage", 8: "nuage (probabilité moyenne)",
                9: "nuage (probabilité forte)", 10: "cirrus"}
EXPORTS = [
    ("GeoTIFF", "Cartes des classes et des changements, géoréférencées (QGIS, ArcGIS)"),
    ("CSV", "Surfaces par classe et par date, matrice de transition (Excel, séparateur « ; »)"),
    ("KML / GeoJSON", "Contours des zones à surveiller (Google Earth, QGIS)"),
    ("PDF", "Rapports avec méthode, résultats, limites et traçabilité"),
    ("PNG / JPEG", "Images de contrôle et aperçus en couleurs réelles"),
]


@bp.route("/parametres", methods=["GET", "POST"])
@connexion_requise
def page():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "nom":
            nom = request.form.get("nom", "").strip()[:80]
            if not nom:
                flash("Le nom ne peut pas être vide.", "warning")
            else:
                bd().execute("UPDATE utilisateur SET nom = ? WHERE id = ?", (nom, g.utilisateur["id"]))
                bd().commit()
                flash("Nom mis à jour.", "success")
        elif action == "mot_de_passe":
            ligne = bd().execute("SELECT mot_de_passe FROM utilisateur WHERE id = ?",
                                 (g.utilisateur["id"],)).fetchone()
            nouveau = request.form.get("nouveau", "")
            if not check_password_hash(ligne["mot_de_passe"], request.form.get("actuel", "")):
                flash("Mot de passe actuel incorrect.", "danger")
            elif len(nouveau) < config.LONGUEUR_MIN_MOT_DE_PASSE:
                flash(f"Le nouveau mot de passe doit contenir au moins "
                      f"{config.LONGUEUR_MIN_MOT_DE_PASSE} caractères.", "danger")
            elif nouveau != request.form.get("confirmation", ""):
                flash("Les deux nouveaux mots de passe ne sont pas identiques.", "danger")
            else:
                bd().execute("UPDATE utilisateur SET mot_de_passe = ? WHERE id = ?",
                             (generate_password_hash(nouveau), g.utilisateur["id"]))
                bd().commit()
                flash("Mot de passe modifié.", "success")
        return redirect(url_for("parametres.page"))

    from moteur import config_modele as cm
    from moteur import serie, tracabilite
    return render_template(
        "parametres.html", cm=cm, formules=FORMULES, exports=EXPORTS,
        scl=[(c, LIBELLES_SCL.get(c, "")) for c in [cm.SCL_SANS_DONNEE] + list(cm.SCL_NUAGES)],
        serie=serie, modele=tracabilite.empreinte_modele(), config=config,
        dossiers={"Archives importées": current_app.config["DOSSIER_IMPORTS"],
                  "Résultats": current_app.config["DOSSIER_RESULTATS"],
                  "Base de données": current_app.config["CHEMIN_BASE"]},
        taille_envoi_mo=round(config.TAILLE_MAX_ENVOI / 1048576))
