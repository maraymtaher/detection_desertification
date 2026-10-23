"""
Chaîne complète d'une analyse : archive SAFE -> fichiers de résultats.
C'est cette fonction que l'interface web appellera en tâche de fond.
"""
import json
import time
from pathlib import Path

from moteur.config_modele import EMPRISE_ENTRAINEMENT, LIBELLES_SAISONS, saison
from moteur.inference import charger_modele, predire_zone
from moteur.lecture_safe import lire_produit
from moteur.pretraitement import LecteurProduit
from moteur import resultats, tracabilite


class ErreurAnalyse(Exception):
    """Erreur compréhensible par l'utilisateur."""


def zone_depuis_wgs84(lecteur, ouest, sud, est, nord):
    """Convertit un rectangle en degrés en zone de pixels
    (lig0, col0, lig1, col1), limitée à l'image."""
    from rasterio.warp import transform_bounds
    gauche, bas, droite, haut = transform_bounds(
        "EPSG:4326", lecteur.crs, ouest, sud, est, nord)
    inverse = ~lecteur.transform
    c0, l0 = inverse * (gauche, haut)
    c1, l1 = inverse * (droite, bas)
    lig0, col0 = max(0, int(l0)), max(0, int(c0))
    lig1 = min(lecteur.hauteur, int(round(l1)))
    col1 = min(lecteur.largeur, int(round(c1)))
    if lig1 <= lig0 or col1 <= col0:
        raise ErreurAnalyse("La zone choisie est en dehors de l'image.")
    return lig0, col0, lig1, col1


def sous_carte(carte, origine, fenetre):
    """Partie de la carte située dans une fenêtre de pixels de l'image.
    origine  : (ligne, colonne) du coin haut-gauche de la carte
    fenetre  : (lig0, col0, lig1, col1) dans l'image entière
    Renvoie None si la fenêtre ne recoupe pas la carte."""
    l0, c0 = origine
    haut, gauche = max(fenetre[0] - l0, 0), max(fenetre[1] - c0, 0)
    bas = min(fenetre[2] - l0, carte.shape[0])
    droite = min(fenetre[3] - c0, carte.shape[1])
    if bas <= haut or droite <= gauche:
        return None
    return carte[haut:bas, gauche:droite]


def emprise_produit(chemin_zip):
    """Rectangle (ouest, sud, est, nord) en degrés couvert par l'image."""
    import rasterio
    from rasterio.warp import transform_bounds
    produit = lire_produit(chemin_zip)
    if not produit.valide:
        raise ErreurAnalyse(" ; ".join(produit.erreurs))
    with rasterio.open(produit.chemin_gdal(produit.bandes["B4"])) as src:
        return tuple(transform_bounds(src.crs, "EPSG:4326", *src.bounds))


def analyser(chemin_zip, dossier_sortie, zone_pixels=None, zone_wgs84=None,
             progression=None, etape=None):
    """
    Lance une analyse complète et écrit les résultats dans dossier_sortie :
      classes.tif, carte.png, rvb.jpg, controle.png, statistiques.csv, resume.json

    zone_pixels : (lig0, col0, lig1, col1), ou
    zone_wgs84  : (ouest, sud, est, nord) en degrés.
    Sans zone, toute l'image est traitée.
    progression(faites, total) suit les fenêtres ; etape(nom) signale
    le passage aux phases "verification", "segmentation", "resultats".
    Renvoie le résumé (dictionnaire).
    """
    signaler = etape or (lambda nom: None)
    debut = time.time()
    signaler("verification")
    produit = lire_produit(chemin_zip)
    if not produit.valide:
        raise ErreurAnalyse(" ; ".join(produit.erreurs))

    dossier = Path(dossier_sortie)
    dossier.mkdir(parents=True, exist_ok=True)
    modele = charger_modele()

    with LecteurProduit(produit) as lecteur:
        if zone_wgs84 is not None:
            zone = zone_depuis_wgs84(lecteur, *zone_wgs84)
        elif zone_pixels is not None:
            zone = tuple(zone_pixels)
        else:
            zone = (0, 0, lecteur.hauteur, lecteur.largeur)
        # Fenêtre de pixels de la zone de validité du modèle dans cette image
        try:
            fenetre_validite = zone_depuis_wgs84(lecteur, *EMPRISE_ENTRAINEMENT)
        except ErreurAnalyse:
            fenetre_validite = None
        signaler("segmentation")
        r = predire_zone(lecteur, modele, *zone, progression=progression)
        transform = resultats.transform_zone(lecteur.transform, r["origine"])
        crs = lecteur.crs

    signaler("resultats")
    carte = r["carte"]
    stats = resultats.statistiques(carte)
    emprise = resultats.emprise_wgs84(carte.shape, transform, crs)
    hors = resultats.part_hors_entrainement(
        zone_wgs84 if zone_wgs84 is not None else emprise)

    # Statistiques limitées à la zone de validité, quand la zone analysée
    # la dépasse : ce sont les seules surfaces fiables.
    stats_validite = None
    if hors > 0 and fenetre_validite is not None:
        partie = sous_carte(carte, r["origine"], fenetre_validite)
        if partie is not None:
            stats_validite = resultats.statistiques(partie)

    resultats.ecrire_geotiff(carte, dossier / "classes.tif", transform, crs)
    resultats.ecrire_png_carte(carte, dossier / "carte.png")
    resultats.ecrire_csv(stats, dossier / "statistiques.csv")
    resultats.enregistrer_controle(
        r["rgb"], carte, dossier / "controle.png",
        f"{produit.date}  tuile {produit.tuile}")

    zone_finale = [int(r["origine"][0]), int(r["origine"][1]),
                   int(r["origine"][0] + carte.shape[0]),
                   int(r["origine"][1] + carte.shape[1])]
    # Image en couleurs réelles pour la carte web (non bloquant)
    try:
        from moteur.apercu import ecrire_image_rvb
        ecrire_image_rvb(chemin_zip, zone_finale, dossier / "rvb.jpg")
    except Exception:
        pass

    avertissements = list(produit.avertissements)
    if hors > 0:
        avertissements.append(
            f"{str(hors).replace('.', ',')} % de la zone se trouve hors de la zone d'entraînement "
            "du modèle : résultat non fiable sur cette partie.")

    resume = {
        "produit": produit.nom,
        "date": produit.date,
        "tuile": produit.tuile,
        "saison": LIBELLES_SAISONS[saison(produit.date)],
        "zone_pixels": zone_finale,
        "emprise_wgs84": [round(v, 6) for v in emprise],
        "surface_km2": round(carte.size * 100 / 1e6, 2),
        "part_hors_entrainement": hors,
        "statistiques": stats,
        "statistiques_validite": stats_validite,
        "avertissements": avertissements,
        "duree_secondes": round(time.time() - debut, 1),
        "tracabilite": tracabilite.trace_analyse(produit, zone_finale),
    }
    with open(dossier / "resume.json", "w", encoding="utf-8") as f:
        json.dump(resume, f, ensure_ascii=False, indent=2)
    return resume
