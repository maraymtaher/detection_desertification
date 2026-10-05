# M3 : prédiction U-Net par fenêtres
"""
M3 — Inférence : prédiction du U-Net sur une zone, par fenêtres.

Règles appliquées :
  - toujours à 10 m, fenêtres de 128 x 128 ;
  - les fenêtres se recouvrent et seul leur centre est conservé,
    pour éviter les coutures aux jonctions ;
  - une fenêtre entièrement invalide n'est pas envoyée au modèle ;
  - les pixels invalides valent 255 (non classé) dans la carte.
"""
import numpy as np

from moteur.config_modele import CHEMIN_MODELE, NON_CLASSE, TAILLE_FENETRE
from moteur.pretraitement import grille_fenetres

_modele = None


def charger_modele():
    """Charge le modèle une seule fois, puis le réutilise."""
    global _modele
    if _modele is None:
        import keras
        _modele = keras.models.load_model(CHEMIN_MODELE, compile=False)
    return _modele


def _apercu_rgb(X):
    """Vraies couleurs (B4, B3, B2) d'une fenêtre, sur 0-255.
    La réflectance 0,6 correspond à 255."""
    rgb = X[..., [2, 1, 0]] / 0.6
    return (np.clip(rgb, 0.0, 1.0) * 255).astype(np.uint8)


def predire_zone(lecteur, modele, lig0, col0, lig1, col1,
                 recouvrement=32, lot=8, progression=None):
    """
    Prédit les classes sur la zone [lig0, lig1[ x [col0, col1[
    (indices de pixels à 10 m dans l'image).

    lecteur     : LecteurProduit ouvert
    modele      : objet avec une méthode predict (modèle Keras)
    progression : fonction optionnelle appelée avec (faites, total)

    Renvoie un dictionnaire :
      carte   : (H, L) uint8, classes 0 à 4, 255 = non classé
      rgb     : (H, L, 3) uint8, aperçu en vraies couleurs
      origine : (ligne, colonne) du coin haut-gauche de la carte
    """
    T = TAILLE_FENETRE
    origines = grille_fenetres(lig0, col0, lig1, col1,
                               lecteur.hauteur, lecteur.largeur,
                               taille=T, recouvrement=recouvrement)
    lignes = sorted({l for l, _ in origines})
    colonnes = sorted({c for _, c in origines})
    L0, C0 = lignes[0], colonnes[0]
    H, L = lignes[-1] + T - L0, colonnes[-1] + T - C0

    carte = np.full((H, L), NON_CLASSE, dtype=np.uint8)
    rgb = np.zeros((H, L, 3), dtype=np.uint8)
    marge = recouvrement // 2

    def ecrire(l, c, classes, valide, apercu):
        # Partie conservée : le centre, sauf sur les bords de la zone
        haut = 0 if l == lignes[0] else marge
        bas = T if l == lignes[-1] else T - marge
        gauche = 0 if c == colonnes[0] else marge
        droite = T if c == colonnes[-1] else T - marge
        bloc = np.where(valide, classes, NON_CLASSE).astype(np.uint8)
        yl, xl = l - L0, c - C0
        carte[yl + haut:yl + bas, xl + gauche:xl + droite] = \
            bloc[haut:bas, gauche:droite]
        rgb[yl + haut:yl + bas, xl + gauche:xl + droite] = \
            apercu[haut:bas, gauche:droite]

    attente = []      # fenêtres en attente de prédiction

    def vider():
        if not attente:
            return
        lot_X = np.stack([a[2] for a in attente])
        proba = modele.predict(lot_X, verbose=0)
        for (l, c, X, valide), p in zip(attente, proba):
            ecrire(l, c, np.argmax(p, axis=-1), valide, _apercu_rgb(X))
        attente.clear()

    total = len(origines)
    for n, (l, c) in enumerate(origines, start=1):
        X, valide = lecteur.lire_fenetre(l, c)
        if valide.any():
            attente.append((l, c, X, valide))
            if len(attente) == lot:
                vider()
        if progression:
            progression(n, total)
    vider()

    # Recadrage sur la zone demandée
    y0, x0 = max(lig0 - L0, 0), max(col0 - C0, 0)
    y1, x1 = min(lig1 - L0, H), min(col1 - C0, L)
    if y1 - y0 >= 1 and x1 - x0 >= 1:
        carte, rgb = carte[y0:y1, x0:x1], rgb[y0:y1, x0:x1]
        L0, C0 = L0 + y0, C0 + x0
    return {"carte": carte, "rgb": rgb, "origine": (L0, C0),
            "nb_fenetres": total}