"""
Outils de lecture pour la page d'une analyse :
  - image en couleurs réelles de la zone analysée (couche de la carte) ;
  - valeurs d'un pixel : classe prédite et les 14 canaux vus par le modèle.
"""
import numpy as np

from moteur.config_modele import BANDES_MODELE, INDICES_MODELE
from moteur.foyers import GAIN_RVB
from moteur.lecture_safe import lire_produit

COTE_MAX_RVB = 2048        # pixels de côté de l'image affichée (JPEG léger)


def pas_reduction(hauteur, largeur, cote_max):
    """Un pixel sur `pas` est conservé pour l'affichage."""
    return max(1, -(-max(hauteur, largeur) // cote_max))


# ---------------------------------------------------------------------
# Accès aux fichiers (isolés pour les tests)
# ---------------------------------------------------------------------
def _lire_reduit(chemin_gdal, fenetre, forme):
    """Bande lue sur une fenêtre (l0, c0, l1, c1), réduite à `forme`."""
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import Window
    l0, c0, l1, c1 = fenetre
    with rasterio.open(chemin_gdal) as src:
        return src.read(1, window=Window(c0, l0, c1 - c0, l1 - l0),
                        out_shape=forme, resampling=Resampling.nearest)


def _classe_locale(chemin_classes, lon, lat):
    """(ligne, colonne, valeur) du pixel de la carte des classes situé
    au point (lon, lat), ou None si le point est hors de la carte."""
    import rasterio
    from rasterio.warp import transform
    with rasterio.open(chemin_classes) as src:
        xs, ys = transform("EPSG:4326", src.crs, [lon], [lat])
        ligne, colonne = src.index(xs[0], ys[0])
        if not (0 <= ligne < src.height and 0 <= colonne < src.width):
            return None
        valeur = src.read(1, window=((ligne, ligne + 1), (colonne, colonne + 1)))[0, 0]
    return int(ligne), int(colonne), int(valeur)


def _canaux_pixel(produit, ligne, colonne):
    """Les 14 canaux du pixel (ligne, colonne) de la tuile, calculés par la
    même chaîne de prétraitement que pour la segmentation."""
    from moteur.pretraitement import LecteurProduit
    with LecteurProduit(produit) as lecteur:
        if not (0 <= ligne < lecteur.hauteur and 0 <= colonne < lecteur.largeur):
            raise ValueError("Pixel hors de l'image.")
        l0 = min(ligne - ligne % 2, lecteur.hauteur - 2)
        c0 = min(colonne - colonne % 2, lecteur.largeur - 2)
        X, valide = lecteur.lire_fenetre(l0, c0, 2)
    return X[ligne - l0, colonne - c0], bool(valide[ligne - l0, colonne - c0])


# ---------------------------------------------------------------------
# Fonctions utilisées par l'application
# ---------------------------------------------------------------------
def ecrire_image_rvb(chemin_zip, zone_pixels, chemin, cote_max=COTE_MAX_RVB):
    """Image JPEG en couleurs réelles (B4, B3, B2) de la zone analysée.
    Même rendu que les images des zones à surveiller : réflectance x 2,5."""
    from PIL import Image
    produit = lire_produit(chemin_zip)
    if not produit.valide:
        raise ValueError(" ; ".join(produit.erreurs))
    l0, c0, l1, c1 = (int(v) for v in zone_pixels)
    pas = pas_reduction(l1 - l0, c1 - c0, cote_max)
    forme = (-(-(l1 - l0) // pas), -(-(c1 - c0) // pas))
    canaux = []
    for nom in ("B4", "B3", "B2"):
        v = _lire_reduit(produit.chemin_gdal(produit.bandes[nom]), (l0, c0, l1, c1), forme)
        canaux.append(np.maximum(v.astype(np.float32) + produit.decalages.get(nom, 0.0), 0)
                      / produit.quantification)
    rvb = (np.clip(np.stack(canaux, axis=-1) * GAIN_RVB, 0, 1) * 255).astype(np.uint8)
    Image.fromarray(rvb).save(chemin, quality=88)
    return chemin


def valeurs_pixel(chemin_zip, chemin_classes, zone_pixels, lon, lat):
    """Classe prédite et canaux d'entrée du pixel situé en (lon, lat).
    Renvoie None si le point est hors de la zone analysée."""
    trouve = _classe_locale(chemin_classes, lon, lat)
    if trouve is None:
        return None
    ligne_locale, colonne_locale, classe = trouve
    ligne = int(zone_pixels[0]) + ligne_locale
    colonne = int(zone_pixels[1]) + colonne_locale
    resultat = {"ligne": ligne, "colonne": colonne, "classe": classe,
                "valide": None, "bandes": None, "indices": None}
    produit = lire_produit(chemin_zip)
    if not produit.valide:
        return resultat                      # archive absente : la classe seule
    x, valide = _canaux_pixel(produit, ligne, colonne)
    resultat["valide"] = valide
    if valide:
        noms = [n for n, _, _ in BANDES_MODELE]
        resultat["bandes"] = {n: round(float(x[i]), 4) for i, n in enumerate(noms)}
        resultat["indices"] = {n: round(float(x[len(noms) + i]), 4)
                               for i, n in enumerate(INDICES_MODELE)}
    return resultat


def ecrire_apercu_tuile(chemin_zip, chemin, cote_max=480):
    """Petit aperçu en couleurs réelles de la tuile entière (page « Images »)."""
    import rasterio
    produit = lire_produit(chemin_zip)
    if not produit.valide:
        raise ValueError(" ; ".join(produit.erreurs))
    with rasterio.open(produit.chemin_gdal(produit.bandes["B4"])) as src:
        hauteur, largeur = src.height, src.width
    return ecrire_image_rvb(chemin_zip, (0, 0, hauteur, largeur), chemin, cote_max)


# ---------------------------------------------------------------------
# Contrôle d'une image avant analyse (page « Prétraitement »)
# ---------------------------------------------------------------------
BANDES_APERCU = [("B2", "Bleu"), ("B3", "Vert"), ("B4", "Rouge"),
                 ("B8", "Proche infrarouge"), ("B11", "Infrarouge court 1"), ("B12", "Infrarouge court 2")]
COTE_CONTROLE = 400


def _lire_entier(chemin_gdal, forme):
    """Bande entière, réduite à `forme` (lecture rapide par les aperçus du JP2)."""
    import rasterio
    from rasterio.enums import Resampling
    with rasterio.open(chemin_gdal) as src:
        return src.read(1, out_shape=forme, resampling=Resampling.nearest)


def _taille(chemin_gdal):
    import rasterio
    with rasterio.open(chemin_gdal) as src:
        return src.height, src.width


def _png_gris(valeurs, valide, chemin):
    """Bande en niveaux de gris, étirée entre les centiles 2 et 98."""
    from PIL import Image
    image = np.zeros(valeurs.shape, dtype=np.uint8)
    if valide.any():
        bas, haut = np.percentile(valeurs[valide], [2, 98])
        if haut > bas:
            image = (np.clip((valeurs - bas) / (haut - bas), 0, 1) * 255).astype(np.uint8)
    image[~valide] = 0
    Image.fromarray(image).save(chemin)


def controler_produit(chemin_zip, dossier):
    """Contrôle d'une image sur la tuile entière, à résolution réduite :
    aperçus des bandes, part de nuages et de données manquantes (couche SCL),
    médianes des indices. Écrit les PNG et controle.json dans `dossier`.
    Les valeurs sont calculées sur un sous-échantillon (environ 400 x 400
    pixels) : ce sont des ordres de grandeur pour contrôler l'image."""
    import json
    from pathlib import Path
    from moteur.config_modele import SCL_NUAGES, SCL_SANS_DONNEE
    produit = lire_produit(chemin_zip)
    if not produit.valide:
        raise ValueError(" ; ".join(produit.erreurs))
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    h, w = _taille(produit.chemin_gdal(produit.bandes["B4"]))
    forme = (COTE_CONTROLE, max(1, round(COTE_CONTROLE * w / h)))

    scl = _lire_entier(produit.chemin_gdal(produit.scl), forme)
    donnee = scl != SCL_SANS_DONNEE
    nuage = np.isin(scl, SCL_NUAGES)
    valide = donnee & ~nuage

    refl = {}
    for nom, _ in BANDES_APERCU:
        v = _lire_entier(produit.chemin_gdal(produit.bandes[nom]), forme).astype(np.float32)
        refl[nom] = np.maximum(v + produit.decalages.get(nom, 0.0), 0) / produit.quantification
        _png_gris(refl[nom], valide, dossier / f"bande_{nom}.png")

    def indice(a, b):
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(a + b != 0, (a - b) / (a + b), np.nan)
        r = r[valide & np.isfinite(r)]
        return round(float(np.median(r)), 3) if r.size else None

    stats = {
        "produit": produit.nom, "date": produit.date, "tuile": produit.tuile,
        "taille_pixels": [int(h), int(w)], "echantillon": list(forme),
        "part_nuages": round(100.0 * float(nuage[donnee].mean()), 1) if donnee.any() else None,
        "part_sans_donnee": round(100.0 * float((~donnee).mean()), 1),
        "part_valide": round(100.0 * float(valide.mean()), 1),
        "indices": {
            "NDVI": indice(refl["B8"], refl["B4"]),
            "NDWI": indice(refl["B3"], refl["B8"]),
            "BSI": indice(refl["B11"] + refl["B4"], refl["B8"] + refl["B2"]),
        },
        "decalages": {k: float(v) for k, v in produit.decalages.items()},
        "bandes_presentes": sorted(produit.bandes),
        "avertissements": list(produit.avertissements),
    }
    with open(dossier / "controle.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    return stats


def ecrire_png_ndvi(chemin_ndvi, chemin, cote_max=2048):
    """Carte du NDVI (fichier ndvi.tif d'une analyse, NDVI x 10000) en couleurs :
    brun (sol nu) -> jaune -> vert (végétation dense) ; transparent sans donnée."""
    from PIL import Image
    import rasterio
    from rasterio.enums import Resampling
    with rasterio.open(chemin_ndvi) as src:
        pas = pas_reduction(src.height, src.width, cote_max)
        v = src.read(1, out_shape=(-(-src.height // pas), -(-src.width // pas)),
                     resampling=Resampling.nearest)
    return _colorer_ndvi(v, chemin)


def _colorer_ndvi(v, chemin):
    from PIL import Image
    sans = v == -32768
    x = np.clip(v.astype(np.float32) / 10000.0, -0.1, 0.7)
    t = (x + 0.1) / 0.8                                  # 0 -> NDVI -0,1 ; 1 -> NDVI 0,7
    points = np.array([[0.0, 140, 81, 10], [0.35, 223, 194, 125], [0.55, 246, 232, 128],
                       [0.75, 120, 198, 121], [1.0, 0, 104, 55]], dtype=np.float32)
    rvba = np.zeros(v.shape + (4,), dtype=np.uint8)
    for k in range(3):
        rvba[..., k] = np.interp(t, points[:, 0], points[:, k + 1]).astype(np.uint8)
    rvba[..., 3] = np.where(sans, 0, 255)
    Image.fromarray(rvba).save(chemin)
    return chemin
