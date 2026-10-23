"""
Calculs de synthèse partagés par le tableau de bord, la page
« Validation et qualité » et les rapports PDF.
"""
import json
import math
from pathlib import Path

from app.modeles_bd import bd

from moteur.serie import (AMELIORATION, CONFIRMEE, DEGRADATION,
                          VERSION as VERSION_METHODE)

TITRES_REPONSE = {
    "favorable": "Non, pas à l'échelle de la zone.",
    "alerte": "Oui, des signes de dégradation dominent : à vérifier sur le terrain.",
    "mitige": "Résultat mitigé : pertes et gains de végétation coexistent.",
}
AVIS = {
    "visible": "Dégradation visible sur les images",
    "non_visible": "Pas de dégradation visible",
    "incertain": "Incertain",
}


def wilson(k, n, z=1.96):
    """Intervalle de confiance à 95 % (méthode de Wilson) d'une proportion k/n,
    en pourcentage. Adapté aux petits effectifs, contrairement à p ± 1,96·σ."""
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    marge = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return round(100 * max(0.0, centre - marge), 1), round(100 * min(1.0, centre + marge), 1)


def _hectares(categories, code):
    return next((c["hectares"] for c in categories if c["code"] == code), 0.0)


def reponse_suivi(resume):
    """Réponse simple d'un suivi (méthode version 3, règle du mémoire),
    identique dans la page du suivi, le tableau de bord et le rapport PDF.

    Indicateurs comparés :
      - pertes de végétation confirmées (U-Net + baisse du NDVI > 0,10,
        taches >= 0,5 ha) contre gains de végétation >= 0,5 ha ;
      - tendance du NDVI médian (Mann-Kendall, 0,05) ;
      - tendance des terres nues (sol nu + sable/dune) hors eau.
    favorable : pertes confirmées < gains, sans baisse du NDVI ni hausse
                des terres nues ;
    alerte    : hausse significative des terres nues, ou pertes confirmées
                > gains avec baisse significative du NDVI ;
    mitigé    : tous les autres cas."""
    if not resume or resume.get("version") != VERSION_METHODE or not resume["bilans"]["synthese"]["disponible"]:
        return None
    b = resume["bilans"]["synthese"]
    confirmee = _hectares(b["categories"], CONFIRMEE)
    non_confirmee = _hectares(b["categories"], DEGRADATION)
    amel = _hectares(b["categories"], AMELIORATION)
    deg = confirmee
    ndvi = resume.get("ndvi") or {}
    seuil = resume["niveau_signification"]
    significatif = ndvi.get("p") is not None and ndvi["p"] < seuil
    ndvi_baisse = significatif and ndvi["s"] < 0
    ndvi_hausse = significatif and ndvi["s"] > 0
    nues = (resume.get("tendances_terre") or [None] * 6)[5] or {}
    nues_hausse = nues.get("sens") == "hausse significative"
    nues_baisse = nues.get("sens") == "baisse significative"
    if nues_hausse or (deg > amel and ndvi_baisse):
        ton = "alerte"
    elif deg < amel and not ndvi_baisse:
        ton = "favorable"
    else:
        ton = "mitige"
    total = b["hectares_compares"]
    validite = resume.get("validite") or {}
    return {
        "ton": ton, "titre": TITRES_REPONSE[ton],
        "terre_ha": total, "zone_ha": total,
        "degradation_ha": deg, "confirmee_ha": confirmee, "amelioration_ha": amel,
        "non_confirmee_ha": non_confirmee,
        "candidate_ha": (resume.get("detail") or {}).get("pertes_candidates_ha", confirmee + non_confirmee),
        "part_degradation": round(100 * deg / total, 2) if total else 0,
        "part_amelioration": round(100 * amel / total, 2) if total else 0,
        "ndvi_sens": "hausse" if ndvi_hausse else "baisse" if ndvi_baisse else "stable",
        "ndvi_p": ndvi.get("p"),
        "ndvi_debut": (ndvi.get("medianes") or [None])[0],
        "ndvi_fin": (ndvi.get("medianes") or [None])[-1],
        "nues_sens": "hausse" if nues_hausse else "baisse" if nues_baisse else "stable",
        "nues_p": nues.get("p"),
        "validite_statut": validite.get("statut"),
        "validite_part": validite.get("part_zone"),
        "debut": resume["dates"][0], "fin": resume["dates"][-1], "nb_dates": resume["nb_dates"],
    }


def zones_du_suivi(suivi):
    """Résultat des zones à surveiller et avis humains d'un suivi (ou None)."""
    etat = bd().execute("SELECT statut FROM zones WHERE suivi_id = ?", (suivi["id"],)).fetchone()
    if not etat or etat["statut"] != "terminee" or not suivi["dossier"]:
        return None
    chemin = Path(suivi["dossier"]) / "foyers" / "foyers.json"
    if not chemin.exists():
        return None
    with open(chemin, encoding="utf-8") as f:
        resultat = json.load(f)
    avis = {a["numero"]: dict(a) for a in bd().execute(
        "SELECT * FROM avis WHERE suivi_id = ?", (suivi["id"],))}
    comptes = {cle: sum(1 for a in avis.values() if a["avis"] == cle) for cle in AVIS}
    verifies = sum(comptes.values())
    return {
        "resultat": resultat, "avis": avis, "comptes": comptes,
        "verifies": verifies, "total": resultat["nb_foyers"],
        "part_visible": round(100 * comptes["visible"] / verifies, 1) if verifies else None,
        "intervalle": wilson(comptes["visible"], verifies),
    }
