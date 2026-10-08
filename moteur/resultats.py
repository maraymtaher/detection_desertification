"""
M4 — Résultats : statistiques, fichiers exportés et image de contrôle.
"""
import csv

import numpy as np

from moteur.config_modele import (
    CLASSES, COULEURS, EMPRISE_ENTRAINEMENT, NON_CLASSE, RESOLUTION)

HECTARES_PAR_PIXEL = RESOLUTION * RESOLUTION / 10000.0   # 0,01 ha à 10 m


def _couleur(hexa):
    return tuple(int(hexa[i:i + 2], 16) for i in (1, 3, 5))


# ---------------------------------------------------------------------
# Statistiques
# ---------------------------------------------------------------------
def statistiques(carte):
    """Surface et pourcentage par classe, calculés sur les pixels valides.
    La part non classée est donnée à part, sans seuil d'alerte."""
    valides = int((carte != NON_CLASSE).sum())
    lignes = []
    for i, nom in enumerate(CLASSES):
        n = int((carte == i).sum())
        lignes.append({
            "classe": nom,
            "pixels": n,
            "hectares": round(n * HECTARES_PAR_PIXEL, 2),
            "pourcentage": round(100.0 * n / valides, 2) if valides else 0.0,
        })
    return {
        "classes": lignes,
        "pixels_valides": valides,
        "hectares_valides": round(valides * HECTARES_PAR_PIXEL, 2),
        "part_non_classee": round(100.0 * (carte.size - valides) / carte.size, 2),
    }


def ecrire_csv(stats, chemin):
    """Tableau des statistiques (séparateur point-virgule, lisible par Excel)."""
    with open(chemin, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["classe", "pixels", "hectares", "pourcentage"])
        for c in stats["classes"]:
            w.writerow([c["classe"], c["pixels"], c["hectares"], c["pourcentage"]])
        w.writerow([])
        w.writerow(["part_non_classee_pourcent", stats["part_non_classee"]])
    return chemin


# ---------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------
def image_classes(carte):
    """Carte des classes en couleurs (H, L, 3). Non classé = gris clair."""
    img = np.full(carte.shape + (3,), 220, dtype=np.uint8)
    for i, hexa in enumerate(COULEURS):
        img[carte == i] = _couleur(hexa)
    return img


def _reduire(tableau, cote_max):
    """Sous-échantillonne un tableau trop grand (un pixel sur n) pour
    l'affichage. Le GeoTIFF, lui, reste toujours en pleine résolution."""
    pas = max(1, -(-max(tableau.shape[0], tableau.shape[1]) // cote_max))
    return tableau[::pas, ::pas]


def ecrire_png_carte(carte, chemin, cote_max=4096):
    """PNG pour la carte web : une couleur par classe, pixels non classés
    transparents. Limité à cote_max pixels de côté pour rester léger."""
    from PIL import Image
    carte = _reduire(carte, cote_max)
    rgba = np.zeros(carte.shape + (4,), dtype=np.uint8)
    rgba[..., :3] = image_classes(carte)
    rgba[..., 3] = np.where(carte == NON_CLASSE, 0, 255)
    Image.fromarray(rgba).save(chemin)
    return chemin


def enregistrer_controle(rgb, carte, chemin, titre="", cote_max=2048):
    """Image PNG : vraies couleurs à gauche, classification à droite,
    légende en dessous. Sert au contrôle visuel."""
    rgb, carte = _reduire(rgb, cote_max), _reduire(carte, cote_max)
    import unicodedata
    from PIL import Image, ImageDraw, ImageFont

    # Police avec accents si disponible (Windows), sinon texte sans accents
    try:
        police = ImageFont.truetype("arial.ttf", 16)
        texte = lambda t: t
    except OSError:
        police = ImageFont.load_default()
        texte = lambda t: unicodedata.normalize("NFKD", t).encode(
            "ascii", "ignore").decode()

    # Étirement de l'aperçu pour qu'il soit lisible à l'œil
    apercu = rgb.astype(np.float32)
    valide = carte != NON_CLASSE
    if valide.any():
        bas, haut = np.percentile(apercu[valide], [2, 98])
        if haut > bas:
            apercu = np.clip((apercu - bas) / (haut - bas), 0, 1) * 255
    apercu = apercu.astype(np.uint8)

    H, L = carte.shape
    espace, bandeau = 20, 70
    toile = Image.new("RGB", (max(2 * L + espace, 1100), H + bandeau), "white")
    toile.paste(Image.fromarray(apercu), (0, 0))
    toile.paste(Image.fromarray(image_classes(carte)), (L + espace, 0))

    dessin = ImageDraw.Draw(toile)
    dessin.text((5, H + 5), texte(f"Vraies couleurs | Classification   {titre}"),
                fill="black", font=police)
    x = 5
    for nom, hexa in list(zip(CLASSES, COULEURS)) + [("Non classé", "#DCDCDC")]:
        dessin.rectangle([x, H + 35, x + 22, H + 57], fill=hexa, outline="black")
        dessin.text((x + 28, H + 38), texte(nom), fill="black", font=police)
        x += 45 + 9 * len(nom)
    toile.save(chemin)
    return chemin


# ---------------------------------------------------------------------
# Géoréférencement
# ---------------------------------------------------------------------
def transform_zone(transform_image, origine):
    """Transformation géographique de la zone, à partir de celle de l'image
    entière et de l'origine (ligne, colonne) de la zone."""
    from rasterio.transform import Affine
    ligne, colonne = origine
    return transform_image * Affine.translation(colonne, ligne)


def ecrire_geotiff(carte, chemin, transform, crs):
    """Carte des classes en GeoTIFF géoréférencé (lisible dans QGIS),
    avec 255 comme valeur « sans donnée » et les couleurs des classes."""
    import rasterio
    profil = {
        "driver": "GTiff", "height": carte.shape[0], "width": carte.shape[1],
        "count": 1, "dtype": "uint8", "crs": crs, "transform": transform,
        "nodata": NON_CLASSE, "compress": "deflate",
    }
    with rasterio.open(chemin, "w", **profil) as dst:
        dst.write(carte, 1)
        dst.write_colormap(1, {i: _couleur(h) + (255,)
                               for i, h in enumerate(COULEURS)})
    return chemin


def emprise_wgs84(forme, transform, crs):
    """Rectangle (ouest, sud, est, nord) en degrés, pour la carte web."""
    from rasterio.transform import array_bounds
    from rasterio.warp import transform_bounds
    gauche, bas, droite, haut = array_bounds(forme[0], forme[1], transform)
    return transform_bounds(crs, "EPSG:4326", gauche, bas, droite, haut)


def part_hors_entrainement(emprise):
    """Pourcentage de la zone (ouest, sud, est, nord) situé hors du
    rectangle d'entraînement du modèle."""
    o, s, e, n = emprise
    eo, es, ee, en = EMPRISE_ENTRAINEMENT
    surface = (e - o) * (n - s)
    if surface <= 0:
        return 100.0
    largeur = max(0.0, min(e, ee) - max(o, eo))
    hauteur = max(0.0, min(n, en) - max(s, es))
    return round(100.0 * (1 - largeur * hauteur / surface), 1)
