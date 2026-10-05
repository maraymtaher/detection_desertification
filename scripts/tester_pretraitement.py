"""
Diagnostic : prétraite une fenêtre d'une archive SAFE, affiche les
statistiques des 14 canaux, puis la prédiction du modèle sur cette fenêtre.

    python scripts/tester_pretraitement.py produit.zip [ligne colonne]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from moteur.config_modele import (
    BANDES_MODELE, CHEMIN_MODELE, CLASSES, INDICES_MODELE)
from moteur.lecture_safe import lire_produit
from moteur.pretraitement import LecteurProduit

if len(sys.argv) not in (2, 4):
    sys.exit("Usage : python scripts/tester_pretraitement.py produit.zip [ligne colonne]")

produit = lire_produit(sys.argv[1])
if not produit.valide:
    sys.exit("Produit refusé : " + " ; ".join(produit.erreurs))
ligne, colonne = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) == 4 else (5376, 5376)

with LecteurProduit(produit) as lecteur:
    print(f"Image : {lecteur.largeur} x {lecteur.hauteur}, {lecteur.crs}")
    X, valide = lecteur.lire_fenetre(ligne, colonne)

print(f"Fenêtre ({ligne}, {colonne}) : forme {X.shape}, type {X.dtype}")
print(f"Pixels valides : {valide.mean() * 100:.1f} %")
print(f"{'Canal':<6}{'min':>9}{'moyenne':>10}{'max':>9}")
noms = [n for n, _, _ in BANDES_MODELE] + INDICES_MODELE
for i, nom in enumerate(noms):
    c = X[..., i]
    print(f"{nom:<6}{c.min():>9.4f}{c.mean():>10.4f}{c.max():>9.4f}")

import keras
modele = keras.models.load_model(CHEMIN_MODELE, compile=False)
proba = modele.predict(X[np.newaxis, ...], verbose=0)[0]
classes = np.argmax(proba, axis=-1)
print("Prédiction du modèle sur cette fenêtre :")
for i, nom in enumerate(CLASSES):
    part = ((classes == i) & valide).sum() / max(valide.sum(), 1) * 100
    print(f"  {nom:<22}{part:>6.1f} %")