# M2 : bandes, indices, masque nuages, normalisation
"""
M2 — Prétraitement : de l'archive SAFE aux fenêtres de 14 canaux.

Reproduit la chaîne d'entraînement du notebook :
  1. retrait du décalage radiométrique (équivalent de la collection
     « harmonisée » de Google Earth Engine) ;
  2. bandes à 20 m rééchantillonnées à 10 m en bilinéaire ;
  3. masque nuages SCL (classes 3, 8, 9, 10) ;
  4. indices NDVI, NDWI, BSI, NDRE, stockés avec 4 décimales ;
  5. division des bandes par 10000 ; pixels invalides mis à 0.

La partie calcul (calculer_canaux, grille_fenetres) n'utilise que NumPy.
La partie lecture (LecteurProduit) utilise Rasterio.
"""
import numpy as np

from moteur.config_modele import (
    BANDES_MODELE, NB_CANAUX, QUANTIFICATION, SCL_NUAGES, SCL_SANS_DONNEE,
    TAILLE_FENETRE)

NOMS_10M = [n for n, _, res in BANDES_MODELE if res == 10]
NOMS_20M = [n for n, _, res in BANDES_MODELE if res == 20]


# ---------------------------------------------------------------------
# 1. Calcul pur (NumPy)
# ---------------------------------------------------------------------
def _difference_normalisee(a, b):
    """(a - b) / (a + b), 0 là où le dénominateur est nul,
    tronqué à 4 décimales comme dans l'export d'entraînement."""
    somme = a + b
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(somme != 0, (a - b) / somme, 0.0)
    return np.trunc(r * 10000.0) / 10000.0


def calculer_canaux(dn, decalages, scl):
    """
    dn        : dict nom de bande -> tableau (H, L) des valeurs brutes,
                toutes déjà sur la grille à 10 m
    decalages : dict nom de bande -> décalage radiométrique (0 ou -1000)
    scl       : tableau (H, L) de la couche SCL sur la grille à 10 m

    Renvoie X (H, L, 14) float32 et valide (H, L) bool.
    """
    # Pixels valides : ni nuage/ombre, ni absence de donnée
    valide = ~np.isin(scl, SCL_NUAGES) & (scl != SCL_SANS_DONNEE)
    for nom in NOMS_10M:
        valide &= dn[nom] != 0

    # Retrait du décalage, valeurs négatives ramenées à 0
    b = {}
    for nom, _, res in BANDES_MODELE:
        v = dn[nom].astype(np.float32) + np.float32(decalages.get(nom, 0.0))
        v = np.maximum(v, 0.0)
        if res == 20:
            v = np.trunc(v)      # l'export d'entraînement stockait des entiers
        b[nom] = v

    ndvi = _difference_normalisee(b["B8"], b["B4"])
    ndwi = _difference_normalisee(b["B3"], b["B8"])
    ndre = _difference_normalisee(b["B8A"], b["B5"])
    plus, moins = b["B11"] + b["B4"], b["B8"] + b["B2"]
    bsi = _difference_normalisee(plus, moins)

    canaux = [b[nom] / QUANTIFICATION for nom, _, _ in BANDES_MODELE]
    canaux += [ndvi, ndwi, bsi, ndre]
    X = np.stack(canaux, axis=-1).astype(np.float32)
    X[~valide] = 0.0             # comme les pixels masqués à l'entraînement
    assert X.shape[-1] == NB_CANAUX
    return X, valide


def grille_fenetres(lig0, col0, lig1, col1, hauteur, largeur,
                    taille=TAILLE_FENETRE, recouvrement=0):
    """
    Origines (ligne, colonne) des fenêtres couvrant la zone
    [lig0, lig1[ x [col0, col1[ d'une image hauteur x largeur.
    Les origines sont paires, pour rester alignées sur la grille à 20 m.
    La dernière fenêtre de chaque rangée est décalée vers l'intérieur
    plutôt que de dépasser de l'image.
    """
    if hauteur < taille or largeur < taille:
        raise ValueError("Image plus petite qu'une fenêtre.")
    pas = taille - recouvrement
    if pas <= 0 or pas % 2:
        raise ValueError("Le recouvrement doit être pair et inférieur à la taille.")

    def origines(debut, fin, limite):
        debut = max(0, min(debut - debut % 2, limite - taille))
        fin = max(fin, debut + taille)
        fin = min(fin, limite)
        positions = list(range(debut, max(fin - taille, debut) + 1, pas))
        dernier = fin - taille
        dernier -= dernier % 2
        if positions[-1] < dernier:
            positions.append(dernier)
        return positions

    lignes = origines(lig0, lig1, hauteur)
    colonnes = origines(col0, col1, largeur)
    return [(l, c) for l in lignes for c in colonnes]


# ---------------------------------------------------------------------
# 2. Lecture dans l'archive (Rasterio)
# ---------------------------------------------------------------------
class LecteurProduit:
    """Ouvre les 10 bandes et la couche SCL d'un ProduitSafe et fournit
    des fenêtres prêtes pour le modèle.

        with LecteurProduit(produit) as lecteur:
            X, valide = lecteur.lire_fenetre(ligne, colonne)
    """
    MARGE_20M = 2   # pixels à 20 m lus en plus autour, pour le bilinéaire

    def __init__(self, produit):
        if not produit.valide:
            raise ValueError("Produit invalide : " + " ; ".join(produit.erreurs))
        self.produit = produit
        self._src = {}

    def __enter__(self):
        import rasterio
        p = self.produit
        for nom, chemin in p.bandes.items():
            self._src[nom] = rasterio.open(p.chemin_gdal(chemin))
        self._src["SCL"] = rasterio.open(p.chemin_gdal(p.scl))
        ref = self._src["B4"]
        self.hauteur, self.largeur = ref.height, ref.width
        self.crs, self.transform = ref.crs, ref.transform
        return self

    def __exit__(self, *exc):
        for src in self._src.values():
            src.close()
        self._src = {}

    def _lire_20m(self, nom, ligne, colonne, taille):
        """Lit une bande à 20 m et la ramène à 10 m en bilinéaire."""
        from rasterio.enums import Resampling
        from rasterio.windows import Window
        src = self._src[nom]
        l2, c2, n = ligne // 2, colonne // 2, taille // 2
        m = self.MARGE_20M
        r0, r1 = max(l2 - m, 0), min(l2 + n + m, src.height)
        k0, k1 = max(c2 - m, 0), min(c2 + n + m, src.width)
        bloc = src.read(
            1, window=Window(k0, r0, k1 - k0, r1 - r0),
            out_shape=((r1 - r0) * 2, (k1 - k0) * 2),
            resampling=Resampling.bilinear).astype(np.float32)
        haut, gauche = (l2 - r0) * 2, (c2 - k0) * 2
        return bloc[haut:haut + taille, gauche:gauche + taille]

    def lire_fenetre(self, ligne, colonne, taille=TAILLE_FENETRE):
        """Fenêtre de `taille` pixels dont le coin haut-gauche est
        (ligne, colonne) sur la grille à 10 m. Coordonnées paires."""
        from rasterio.windows import Window
        if ligne % 2 or colonne % 2 or taille % 2:
            raise ValueError("Ligne, colonne et taille doivent être pairs.")
        if (ligne < 0 or colonne < 0 or ligne + taille > self.hauteur
                or colonne + taille > self.largeur):
            raise ValueError("Fenêtre hors de l'image.")

        dn = {}
        for nom in NOMS_10M:
            dn[nom] = self._src[nom].read(
                1, window=Window(colonne, ligne, taille, taille))
        for nom in NOMS_20M:
            dn[nom] = self._lire_20m(nom, ligne, colonne, taille)

        scl20 = self._src["SCL"].read(
            1, window=Window(colonne // 2, ligne // 2, taille // 2, taille // 2))
        scl = np.repeat(np.repeat(scl20, 2, axis=0), 2, axis=1)

        return calculer_canaux(dn, self.produit.decalages, scl)