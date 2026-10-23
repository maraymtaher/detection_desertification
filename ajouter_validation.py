"""Ajoute à DeepDesert les résultats de validation du mémoire.

- page Évaluation : précision et rappel moyens (mémoire, tableau 4.2) et
  résultats du contrôle visuel en aveugle (mémoire, tableau 4.7) ;
- définition de « perte confirmée » sous chaque résultat de détection ;
- limites du modèle mises à jour (test sur une zone voisine).

Utilisation (dans le dossier deepdesert, application arrêtée) :
    python ajouter_validation.py
"""
import re
import shutil
from pathlib import Path

RACINE = Path(__file__).resolve().parent
SAUVEGARDE = RACINE / "sauvegarde_avant_validation"
bilan = []


def sauvegarder(chemin):
    cible = SAUVEGARDE / chemin.relative_to(RACINE)
    if not cible.exists():
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(chemin, cible)


def remplacer(fichier, ancien, nouveau, nom, regex=False, marqueur=None):
    """Remplace « ancien » par « nouveau » dans « fichier » (chemin relatif)."""
    chemin = RACINE / fichier
    if not chemin.exists():
        bilan.append(("INTROUVABLE", nom, f"fichier absent : {fichier}"))
        return
    texte = chemin.read_text(encoding="utf-8")
    if regex:
        nouveau_texte, n = re.subn(ancien, nouveau, texte, flags=re.S)
    else:
        n = texte.count(ancien)
        nouveau_texte = texte.replace(ancien, nouveau)
    if n == 0:
        deja = (marqueur or nouveau) in texte
        bilan.append(("DÉJÀ FAIT" if deja else "INTROUVABLE", nom, fichier))
        return
    sauvegarder(chemin)
    chemin.write_text(nouveau_texte, encoding="utf-8")
    bilan.append(("OK", nom, f"{fichier} ({n} remplacement(s))"))



import json

Q = "app/templates/qualite.html"
# 1. Précision et rappel moyens (moyenne des 5 classes, mémoire tableau 4.2)
remplacer(Q, '<div><small>Précision moyenne</small><b class="absent">non mesurée</b><small>absente des sorties enregistrées</small></div>',
          '<div><small>Précision moyenne</small><b>0,68</b><small>moyenne des 5 classes (mémoire, tableau 4.2)</small></div>',
          "1. Précision moyenne")
remplacer(Q, '<div><small>Rappel moyen</small><b class="absent">non mesuré</b><small>absent des sorties enregistrées</small></div>',
          '<div><small>Rappel moyen</small><b>0,78</b><small>moyenne des 5 classes (mémoire, tableau 4.2)</small></div>',
          "1. Rappel moyen")
remplacer(Q, "comparaison avec des masques de référence automatiques.{% endblock %}",
          "comparaison avec des masques de référence automatiques · emprise de Bol.{% endblock %}",
          "1. Sous-titre de la page Évaluation")

# 2. Contrôle visuel en aveugle
BLOC = """{# ================= Contrôle visuel en aveugle ================= #}
<section class="panneau" style="margin-top:20px">
  <header><h2>Contrôle visuel en aveugle des pertes détectées</h2>
    <p>80 points tirés au hasard (20 par catégorie) dans la carte de synthèse de février · 69 interprétés sans connaître la catégorie du système</p></header>
  <div class="corps">
    <table class="donnees">
      <thead><tr><th>Catégorie de la carte</th><th class="num">Points interprétés</th><th class="num">Pertes observées</th><th class="num">IC à 95 % (Wilson)</th></tr></thead>
      <tbody>
        <tr><td>Perte confirmée</td><td class="num">11</td><td class="num"><b>7 (63,6 %)</b></td><td class="num">35,4 à 84,8 %</td></tr>
        <tr><td>Perte non confirmée</td><td class="num">18</td><td class="num">1 (5,6 %)</td><td class="num">1,0 à 25,8 %</td></tr>
        <tr><td>Gain de végétation</td><td class="num">20</td><td class="num">0 (0,0 %)</td><td class="num">0,0 à 16,1 %</td></tr>
        <tr><td>Stable</td><td class="num">20</td><td class="num">0 (0,0 %)</td><td class="num">0,0 à 16,1 %</td></tr>
      </tbody>
    </table>
    <p class="note">Lecture : environ deux pertes confirmées sur trois sont des pertes visibles sur les images, contre une perte non confirmée sur dix-huit.
      Les intervalles ne se chevauchent pas : la confirmation par le NDVI apporte une information réelle. Contrôle réalisé par une seule interprète
      sur des images Sentinel-2 à 10 m, sans vérité de terrain ; avec 11 points, la précision des pertes confirmées reste imprécise.</p>
  </div>
</section>

{# ================= 5. Limites ================= #}"""
if (RACINE / Q).exists() and "Contrôle visuel en aveugle des pertes détectées" in (RACINE / Q).read_text(encoding="utf-8"):
    bilan.append(("DÉJÀ FAIT", "2. Résultats du contrôle en aveugle", Q))
else:
    remplacer(Q, "{# ================= 5. Limites ================= #}", BLOC,
              "2. Résultats du contrôle en aveugle", marqueur="Contrôle visuel en aveugle des pertes détectées")

# 3. Définition de « perte confirmée » sous chaque résultat de détection
NOTE = ('<p class="note" style="margin-top:12px">« Perte confirmée » : passage de la végétation à la surface nue détecté par le U-Net, '
        "accompagné d'une baisse du NDVI supérieure à 0,10, en taches d'au moins 0,5 ha. C'est un critère de cohérence entre deux mesures, "
        "et non une vérification de terrain ; lors du contrôle en aveugle, 63,6 % des pertes confirmées étaient visibles sur les images.</p>\n\n")
for page in ("detection_resultat.html", "suivi.html"):
    remplacer(f"app/templates/{page}",
              r"(</section>\n\n)(\{% if (?:v|false) %\}\n<div class=\"message \{\{ 'success' if v\.statut)",
              r"\1" + NOTE.replace("\\", "\\\\") + r"\2",
              f"3. Définition de « perte confirmée » ({page})", regex=True, marqueur="« Perte confirmée » : passage")

# 4. Limites du modèle (page Évaluation et rapport PDF)
J = RACINE / "moteur/evaluation_modele.json"
if J.exists():
    d = json.loads(J.read_text(encoding="utf-8"))
    lim = d.get("limites", [])
    ancien = ("Entraînement, validation et test portent sur la même emprise : "
              "la capacité à généraliser à d'autres régions n'est pas mesurée.")
    nouveau = ("Entraînement, validation et test portent sur la même emprise (Bol). Sur une zone voisine jamais vue, "
               "l'accord avec Dynamic World passe d'un Kappa de 0,829 à 0,490 : la précision ailleurs autour du lac reste à évaluer.")
    definition = ("Une « perte confirmée » associe une transition du U-Net et une baisse du NDVI : c'est un critère de cohérence, "
                  "pas une vérification de terrain (63,6 % de pertes visibles lors du contrôle en aveugle, sur 11 points).")
    change = False
    if ancien in lim:
        lim[lim.index(ancien)] = nouveau; change = True
    if definition not in lim:
        lim.append(definition); change = True
    if change:
        sauvegarder(J)
        d["limites"] = lim
        J.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
        bilan.append(("OK", "4. Limites du modèle mises à jour", "moteur/evaluation_modele.json"))
    else:
        bilan.append(("DÉJÀ FAIT", "4. Limites du modèle mises à jour", "moteur/evaluation_modele.json"))
else:
    bilan.append(("INTROUVABLE", "4. Limites du modèle", "fichier absent : moteur/evaluation_modele.json"))

# ---------------------------------------------------------------------
print("\nBilan des modifications\n" + "-" * 60)
for etat, nom, detail in bilan:
    print(f"[{etat:11}] {nom} — {detail}")
introuvables = [b for b in bilan if b[0] == "INTROUVABLE"]
print("-" * 60)
print(f"Sauvegarde des fichiers d'origine : {SAUVEGARDE}")
if introuvables:
    print(f"{len(introuvables)} modification(s) introuvable(s) : envoyez ce bilan pour une correction ciblée.")
else:
    print("Toutes les modifications sont appliquées. Relancez : python run.py")
