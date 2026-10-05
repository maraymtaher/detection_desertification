"""
Diagnostic : pour chaque classe prédite sur une zone, affiche le NDVI et
le BSI moyens, et la part de pixels sous le seuil NDVI de 0,15.
Sert à comprendre POURQUOI le modèle attribue une classe.

    python scripts/diagnostic_classes.py produit.zip ligne colonne hauteur largeur
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from moteur.config_modele import CLASSES
from moteur.inference import charger_modele
from moteur.lecture_safe import lire_produit
from moteur.pretraitement import LecteurProduit, grille_fenetres

if len(sys.argv) != 6:
    sys.exit("Usage : python scripts/diagnostic_classes.py produit.zip "
             "ligne colonne hauteur largeur")

produit = lire_produit(sys.argv[1])
if not produit.valide:
    sys.exit("Produit refusé : " + " ; ".join(produit.erreurs))
lig, col, haut, larg = [int(a) for a in sys.argv[2:6]]
modele = charger_modele()

NDVI, BSI = 10, 12            # positions de ces indices parmi les 14 canaux
ndvi_par_classe = [[] for _ in CLASSES]
bsi_par_classe = [[] for _ in CLASSES]

with LecteurProduit(produit) as lecteur:
    origines = grille_fenetres(lig, col, lig + haut, col + larg,
                               lecteur.hauteur, lecteur.largeur)
    for l, c in origines:
        X, valide = lecteur.lire_fenetre(l, c)
        if not valide.any():
            continue
        classes = np.argmax(modele.predict(X[np.newaxis], verbose=0)[0], axis=-1)
        for i in range(len(CLASSES)):
            m = valide & (classes == i)
            ndvi_par_classe[i].append(X[..., NDVI][m])
            bsi_par_classe[i].append(X[..., BSI][m])

print(f"\n{'Classe prédite':<22}{'pixels':>10}{'NDVI moy.':>11}"
      f"{'BSI moy.':>10}{'NDVI<0,15':>11}")
for i, nom in enumerate(CLASSES):
    ndvi = np.concatenate(ndvi_par_classe[i]) if ndvi_par_classe[i] else np.array([])
    bsi = np.concatenate(bsi_par_classe[i]) if bsi_par_classe[i] else np.array([])
    if ndvi.size == 0:
        print(f"{nom:<22}{0:>10}")
        continue
    print(f"{nom:<22}{ndvi.size:>10}{ndvi.mean():>11.3f}{bsi.mean():>10.3f}"
          f"{(ndvi < 0.15).mean() * 100:>10.1f}%")

print("\nRéférence du notebook (cellule 168, par classe du masque) :")
print("  Végétation dégradée : NDVI 0,280  BSI 0,092")
print("  Sol nu              : NDVI 0,160  BSI 0,200")
print("  Sable/Dune          : NDVI 0,155  BSI 0,216")