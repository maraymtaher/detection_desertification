"""Accueil et tableau de bord environnemental.

Tous les chiffres du tableau de bord sont lus dans les résultats enregistrés
(base de données, resume.json, foyers.json) : aucune valeur n'est estimée."""
import json
import math
from datetime import datetime, timezone

from flask import Blueprint, g, render_template

from app import geometrie, graphiques
from app.indicateurs import AVIS, reponse_suivi, zones_du_suivi
from app.modeles_bd import bd

bp = Blueprint("main", __name__)

DOMAINES = {"valide": "validé", "partiel": "partiel", "indicatif": "indicatif", "hors": "indicatif"}
SAISONS_COURTES = {"pluies": "saison des pluies", "seche_fraiche": "saison sèche fraîche",
                   "seche_chaude": "saison sèche chaude"}


def _saison_courte(date_iso):
    from moteur.config_modele import saison
    try:
        return SAISONS_COURTES.get(saison(date_iso), "saison inconnue")
    except (ValueError, TypeError):
        return "saison inconnue"


DIRECTIONS = ["Est", "Nord-Est", "Nord", "Nord-Ouest", "Ouest", "Sud-Ouest", "Sud", "Sud-Est"]


def direction(centre, emprise):
    """Position d'un point dans la zone d'étude (Nord-Est, Centre…), d'après
    l'angle entre le centre de la zone et ce point."""
    o, s, e, n = emprise
    dx = (centre[0] - (o + e) / 2) / ((e - o) / 2)
    dy = (centre[1] - (s + n) / 2) / ((n - s) / 2)
    if math.hypot(dx, dy) < 0.25:
        return "Centre"
    secteur = int(((math.degrees(math.atan2(dy, dx)) + 360 + 22.5) % 360) // 45)
    return DIRECTIONS[secteur]


def heure_locale(horodatage_utc):
    """« AAAA-MM-JJ HH:MM:SS » (UTC, SQLite) -> date et heure de cette machine."""
    if not horodatage_utc:
        return None
    t = datetime.strptime(horodatage_utc[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return t.astimezone().strftime("%d/%m/%Y %H:%M")


def _analyses_terminees(uid):
    return bd().execute(
        "SELECT a.id, a.resume, a.termine_le, p.date_acquisition, p.nom FROM analyse a "
        "JOIN produit p ON p.id = a.produit_id WHERE a.utilisateur_id = ? AND a.statut = 'terminee' "
        "ORDER BY p.date_acquisition DESC, a.id DESC", (uid,)).fetchall()


@bp.route("/")
def accueil():
    """Tableau de bord (maquette 2) : chiffres lus dans la base et les résultats."""
    if g.utilisateur is None:
        return render_template("accueil.html")
    uid = g.utilisateur["id"]
    from moteur.config_modele import COULEURS
    analyses = _analyses_terminees(uid)
    nb_images = bd().execute("SELECT COUNT(*) FROM produit WHERE utilisateur_id = ?", (uid,)).fetchone()[0]
    mois_courant = datetime.now(timezone.utc).strftime("%Y-%m")
    ce_mois = sum(1 for a in analyses if (a["termine_le"] or "").startswith(mois_courant))
    en_cours = bd().execute("SELECT COUNT(*) FROM analyse WHERE utilisateur_id = ? "
                            "AND statut IN ('en_cours', 'en_attente')", (uid,)).fetchone()[0]

    # Dernière analyse terminée (par date de calcul) et carte la plus récente (par date d'image)
    derniere = max(analyses, key=lambda a: a["termine_le"] or "", default=None)
    recente = analyses[0] if analyses else None
    carte = None
    if recente:
        r = json.loads(recente["resume"])
        st = r["statistiques"]
        carte = {"id": recente["id"], "date": recente["date_acquisition"], "emprise": r["emprise_wgs84"],
                 "classes": st["classes"], "non_classe": st["part_non_classee"],
                 "anneau": graphiques.anneau(
                     [(c["classe"], c["hectares"], couleur, f"{c['pourcentage']:.1f} %".replace(".", ","))
                      for c, couleur in zip(st["classes"], COULEURS)],
                     f"{round(st['hectares_valides'] / 100):,} km²".replace(",", "\u202f"), "(100 %)")}

    # Dernière détection et zones à surveiller (ligne complémentaire)
    dernier = bd().execute(
        "SELECT * FROM suivi WHERE utilisateur_id = ? AND statut = 'terminee' ORDER BY id DESC LIMIT 1",
        (uid,)).fetchone()
    reponse = reponse_suivi(json.loads(dernier["resume"])) if dernier else None
    zones = zones_du_suivi(dernier) if dernier else None
    return render_template(
        "tableau_de_bord.html", nb_images=nb_images, nb_analyses=len(analyses), ce_mois=ce_mois,
        en_cours=en_cours, derniere=derniere,
        derniere_heure=heure_locale(derniere["termine_le"]) if derniere else None,
        carte=carte, dernier=dernier, reponse=reponse, zones=zones,
        saison_detection=_saison_courte(reponse["debut"]) if reponse else None,
        domaine_detection=DOMAINES.get(reponse["validite_statut"], "non précisé") if reponse else None)
