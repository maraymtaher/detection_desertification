"""
Productivité des terres : NDVI d'une date, calculé directement à partir
de l'archive SAFE, sur exactement la même zone de pixels que la carte
des classes de l'analyse.

Le NDVI est calculé comme à l'entraînement du modèle :
  réflectance = (valeur + décalage) ramenée à 0 si négative,
  NDVI = (B8 - B4) / (B8 + B4), tronqué à 4 décimales,
  pixels masqués : SCL 0, 3, 8, 9, 10 et valeurs B4/B8 nulles.
Il est stocké en entier : NDVI x 10000 (int16), -32768 = sans donnée.
"""
import numpy as np

from moteur.config_modele import SCL_NUAGES, SCL_SANS_DONNEE
from moteur.lecture_safe import lire_produit

SANS_DONNEE_NDVI = -32768
LIGNES_PAR_BLOC = 512


def ndvi_bloc(b4, b8, scl, decalage4, decalage8):
    """NDVI x 10000 (int16) d'un bloc ; SANS_DONNEE_NDVI là où c'est masqué."""
    valide = ~np.isin(scl, SCL_NUAGES) & (scl != SCL_SANS_DONNEE) & (b4 != 0) & (b8 != 0)
    r4 = np.maximum(b4.astype(np.float32) + np.float32(decalage4), 0.0)
    r8 = np.maximum(b8.astype(np.float32) + np.float32(decalage8), 0.0)
    somme = r8 + r4
    with np.errstate(divide="ignore", invalid="ignore"):
        ndvi = np.where(somme != 0, (r8 - r4) / somme, 0.0)
    sortie = np.trunc(ndvi * 10000.0).astype(np.int16)
    sortie[~valide] = SANS_DONNEE_NDVI
    return sortie


def calculer_ndvi(chemin_zip, zone_pixels, chemin_sortie, progression=None):
    """Écrit le NDVI de la zone (lig0, col0, lig1, col1) en GeoTIFF."""
    import rasterio
    from rasterio.transform import Affine
    from rasterio.windows import Window

    produit = lire_produit(chemin_zip)
    if not produit.valide:
        raise ValueError(" ; ".join(produit.erreurs))
    lig0, col0, lig1, col1 = (int(v) for v in zone_pixels)
    hauteur, largeur = lig1 - lig0, col1 - col0

    with rasterio.open(produit.chemin_gdal(produit.bandes["B4"])) as s4, \
         rasterio.open(produit.chemin_gdal(produit.bandes["B8"])) as s8, \
         rasterio.open(produit.chemin_gdal(produit.scl)) as sscl:
        profil = {"driver": "GTiff", "height": hauteur, "width": largeur, "count": 1,
                  "dtype": "int16", "crs": s4.crs,
                  "transform": s4.transform * Affine.translation(col0, lig0),
                  "nodata": SANS_DONNEE_NDVI, "compress": "deflate"}
        with rasterio.open(chemin_sortie, "w", **profil) as dst:
            for r in range(0, hauteur, LIGNES_PAR_BLOC):
                h = min(LIGNES_PAR_BLOC, hauteur - r)
                fen = Window(col0, lig0 + r, largeur, h)
                b4 = s4.read(1, window=fen)
                b8 = s8.read(1, window=fen)
                # SCL à 20 m : chaque pixel couvre 2 x 2 pixels à 10 m
                l, c = lig0 + r, col0
                l2, c2 = l // 2, c // 2
                nl = (l + h + 1) // 2 - l2
                nc = (c + largeur + 1) // 2 - c2
                scl20 = sscl.read(1, window=Window(c2, l2, nc, nl), boundless=True, fill_value=0)
                scl = np.repeat(np.repeat(scl20, 2, axis=0), 2, axis=1)
                scl = scl[l % 2:l % 2 + h, c % 2:c % 2 + largeur]
                dst.write(ndvi_bloc(b4, b8, scl, produit.decalages.get("B4", 0.0),
                                    produit.decalages.get("B8", 0.0)),
                          1, window=Window(0, r, largeur, h))
                if progression:
                    progression(r + h, hauteur)
    return chemin_sortie
