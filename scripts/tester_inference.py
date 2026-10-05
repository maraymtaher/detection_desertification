"""
Diagnostic : prédit une zone d'une archive SAFE, mesure le temps,
affiche les statistiques et enregistre une image de contrôle.

    python scripts/tester_inference.py produit.zip [ligne colonne hauteur largeur]

Par défaut : zone de 1024 x 1024 pixels (environ 10 km x 10 km)
dont le coin haut-gauche est (7680, 7680).
"""
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from moteur.inference import charger_modele, predire_zone
from moteur.lecture_safe import lire_produit
from moteur.pretraitement import LecteurProduit
from moteur.resultats import enregistrer_controle, statistiques

if len(sys.argv) not in (2, 6):
    sys.exit("Usage : python scripts/tester_inference.py produit.zip "
             "[ligne colonne hauteur largeur]")

produit = lire_produit(sys.argv[1])
if not produit.valide:
    sys.exit("Produit refusé : " + " ; ".join(produit.erreurs))
lig, col, haut, larg = ([int(a) for a in sys.argv[2:6]]
                        if len(sys.argv) == 6 else (7680, 7680, 1024, 1024))

print("Chargement du modèle...")
modele = charger_modele()


def progression(faites, total):
    if faites % 20 == 0 or faites == total:
        print(f"  {faites}/{total} fenêtres", flush=True)


debut = time.time()
with LecteurProduit(produit) as lecteur:
    r = predire_zone(lecteur, modele, lig, col, lig + haut, col + larg,
                     progression=progression)
duree = time.time() - debut

carte = r["carte"]
km2 = carte.size * 100 / 1e6
print(f"\nZone : {carte.shape[1]} x {carte.shape[0]} pixels, soit {km2:.1f} km²")
print(f"Temps : {duree:.1f} s pour {r['nb_fenetres']} fenêtres "
      f"({duree / r['nb_fenetres']:.2f} s par fenêtre, {duree / km2:.2f} s par km²)")

s = statistiques(carte)
print(f"Part non classée : {s['part_non_classee']} %")
for c in s["classes"]:
    print(f"  {c['classe']:<22}{c['hectares']:>10.1f} ha {c['pourcentage']:>7.2f} %")

sortie = RACINE / "donnees" / "resultats" / f"controle_{produit.date}_{lig}_{col}.png"
sortie.parent.mkdir(parents=True, exist_ok=True)
enregistrer_controle(r["rgb"], carte, sortie, f"{produit.date}  tuile {produit.tuile}")
print(f"Image de contrôle : {sortie}")