"""
Diagnostic : lit une archive SAFE et affiche ce que le système en comprend.
À lancer depuis la racine du projet :
    python scripts/tester_produit.py chemin/vers/produit.zip
"""
import sys
from pathlib import Path

# Rend le dossier "moteur" importable quand on lance ce script directement
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from moteur.lecture_safe import lire_produit

if len(sys.argv) != 2:
    sys.exit("Usage : python scripts/tester_produit.py produit.zip")

p = lire_produit(sys.argv[1])

print(f"Produit    : {p.nom}")
print(f"Satellite  : {p.satellite}   Niveau : {p.niveau}")
print(f"Date       : {p.date}   Tuile : {p.tuile}")
print(f"Traitement : version {p.version_traitement}, "
      f"quantification {p.quantification:.0f}")
print(f"Bandes     : {len(p.bandes)}/10 trouvées, "
      f"SCL {'trouvée' if p.scl else 'absente'}")
if p.decalages:
    print(f"Décalages  : {sorted(set(p.decalages.values()))}")
for a in p.avertissements:
    print(f"AVERTISSEMENT : {a}")
for e in p.erreurs:
    print(f"ERREUR : {e}")
print("=> Produit VALIDE" if p.valide else "=> Produit REFUSÉ")