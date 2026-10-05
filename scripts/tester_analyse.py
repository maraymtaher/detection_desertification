"""
Diagnostic : lance une analyse complète et écrit tous les fichiers
de résultats, comme le fera l'interface web.

    python scripts/tester_analyse.py produit.zip ligne colonne hauteur largeur
"""
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from moteur.analyse import ErreurAnalyse, analyser

if len(sys.argv) != 6:
    sys.exit("Usage : python scripts/tester_analyse.py produit.zip "
             "ligne colonne hauteur largeur")
lig, col, haut, larg = [int(a) for a in sys.argv[2:6]]
dossier = RACINE / "donnees" / "resultats" / f"test_{lig}_{col}_{haut}_{larg}"


def progression(faites, total):
    if faites % 50 == 0 or faites == total:
        print(f"  {faites}/{total} fenêtres", flush=True)


try:
    r = analyser(sys.argv[1], dossier,
                 zone_pixels=(lig, col, lig + haut, col + larg),
                 progression=progression)
except ErreurAnalyse as e:
    sys.exit(f"Analyse refusée : {e}")

print(f"\nProduit : {r['date']}  tuile {r['tuile']}")
print(f"Saison  : {r['saison']}")
print(f"Surface : {r['surface_km2']} km²   Durée : {r['duree_secondes']} s")
o, s, e, n = r["emprise_wgs84"]
print(f"Emprise : longitude {o} à {e}, latitude {s} à {n}")
print(f"Part non classée : {r['statistiques']['part_non_classee']} %")
for c in r["statistiques"]["classes"]:
    print(f"  {c['classe']:<36}{c['hectares']:>10.1f} ha {c['pourcentage']:>7.2f} %")
for a in r["avertissements"]:
    print(f"AVERTISSEMENT : {a}")
print(f"\nFichiers écrits dans : {dossier}")
for f in sorted(dossier.iterdir()):
    print(f"  {f.name}")