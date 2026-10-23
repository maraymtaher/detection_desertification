"""
Validation de la détection — étape 2 : calcul de la précision.

Compare les réponses du carnet (reponses.csv) à la catégorie que le système
avait attribuée à chaque point (cle_NE_PAS_OUVRIR.csv), puis calcule :

  1. pour chaque catégorie de la carte, la part des points où une perte de
     végétation est réellement visible, avec son intervalle de confiance à
     95 % (méthode de Wilson) ;
  2. la précision de la catégorie « perte confirmée » (part des pertes
     confirmées qui sont de vraies pertes) et de la catégorie « stable » ;
  3. la surface de pertes réelles estimée sur la zone et la part captée par
     la carte, selon les estimateurs pour un échantillon stratifié
     d'Olofsson et al. (2014), Remote Sensing of Environment, 148, 42-57.

La vérité de chaque point vient des deux réponses :
  végétation -> sol nu / sable : perte ; sol nu / sable -> végétation : gain ;
  même état : stable ; eau dans une des deux périodes : eau ;
  « illisible » dans une période : point écarté.

Usage :
    python scripts/validation_resultats.py --suivi 4
    python scripts/validation_resultats.py --suivi 4 --sures-seulement
"""
import argparse
import csv
import json
import math
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

NOMS = {"perte_confirmee": "Perte confirmée", "perte_non_confirmee": "Perte non confirmée",
        "gain": "Gain de végétation", "stable": "Stable"}
ORDRE = ["perte_confirmee", "perte_non_confirmee", "gain", "stable"]


def lire_csv(chemin):
    with open(chemin, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def verite(reference, recente):
    if "illisible" in (reference, recente) or not reference or not recente:
        return None
    if "eau" in (reference, recente):
        return "eau" if reference != recente else "stable"
    if reference == "vegetation" and recente == "surface_nue":
        return "perte"
    if reference == "surface_nue" and recente == "vegetation":
        return "gain"
    return "stable"


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    marge = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - marge), min(1.0, centre + marge)


def calculer(cle, reponses, pixels_par_strate, ha_par_pixel, sures_seulement=False):
    rep = {r["id"]: r for r in reponses}
    par_strate = {s: {"n": 0, "perte": 0, "gain": 0, "stable": 0, "eau": 0, "ecartes": 0} for s in ORDRE}
    for ligne in cle:
        s = ligne["strate"]
        if s not in par_strate:
            continue
        r = rep.get(ligne["id"])
        v = None
        if r and not (sures_seulement and r.get("confiance") != "sure"):
            v = verite(r.get("reference", ""), r.get("recente", ""))
        if v is None:
            par_strate[s]["ecartes"] += 1
            continue
        par_strate[s]["n"] += 1
        par_strate[s][v] += 1

    total_pixels = sum(pixels_par_strate.get(s, 0) for s in ORDRE)
    lignes = []
    p_perte, var_perte, contrib = 0.0, 0.0, {}
    for s in ORDRE:
        d = par_strate[s]
        n = d["n"]
        w = pixels_par_strate.get(s, 0) / total_pixels if total_pixels else 0.0
        part = d["perte"] / n if n else None
        ic = wilson(d["perte"], n)
        lignes.append({"strate": s, "nom": NOMS[s], "points_valides": n, "points_ecartes": d["ecartes"],
                       "pertes_vues": d["perte"], "gains_vus": d["gain"], "stables_vus": d["stable"],
                       "eau_vue": d["eau"], "part_pertes": part,
                       "ic95": ic, "poids_surface": w,
                       "hectares_carte": pixels_par_strate.get(s, 0) * ha_par_pixel})
        if n:
            p_perte += w * part
            contrib[s] = w * part
            if n > 1:
                var_perte += w * w * part * (1 - part) / (n - 1)
    surface_totale_ha = total_pixels * ha_par_pixel
    se = math.sqrt(var_perte)
    estimation = {
        "part_pertes_reelles": p_perte,
        "hectares_pertes_reelles": p_perte * surface_totale_ha,
        "ic95_hectares": (max(0.0, (p_perte - 1.96 * se) * surface_totale_ha),
                          (p_perte + 1.96 * se) * surface_totale_ha),
        "part_captee_par_pertes_confirmees": (contrib.get("perte_confirmee", 0.0) / p_perte) if p_perte else None,
        "surface_echantillonnee_ha": surface_totale_ha,
    }
    return lignes, estimation


def pct(v):
    return "—" if v is None else f"{100 * v:.1f} %".replace(".", ",")


def ha(v):
    return f"{v:,.0f}".replace(",", " ")


def main():
    import config
    parser = argparse.ArgumentParser(description="Précision de la détection à partir du carnet")
    parser.add_argument("--suivi", type=int, required=True)
    parser.add_argument("--sures-seulement", action="store_true",
                        help="ne garder que les réponses marquées « sûre »")
    parser.add_argument("--dossier", default=None, help="dossier de validation (par défaut donnees/validation/suivi_N)")
    args = parser.parse_args()
    dossier = Path(args.dossier) if args.dossier else config.DOSSIER_DONNEES / "validation" / f"suivi_{args.suivi}"
    for nom in ("cle_NE_PAS_OUVRIR.csv", "reponses.csv", "tirage.json"):
        if not (dossier / nom).exists():
            sys.exit(f"Fichier manquant : {dossier / nom}"
                     + (" (exportez-le depuis le carnet et placez-le dans ce dossier)" if nom == "reponses.csv" else ""))
    tirage = json.loads((dossier / "tirage.json").read_text(encoding="utf-8"))
    lignes, est = calculer(lire_csv(dossier / "cle_NE_PAS_OUVRIR.csv"), lire_csv(dossier / "reponses.csv"),
                           tirage["pixels_par_strate"], tirage["hectares_par_pixel"], args.sures_seulement)

    texte = [f"# Validation du suivi n° {args.suivi}", "",
             f"Contrôle visuel en aveugle de {sum(l['points_valides'] for l in lignes)} points "
             f"({sum(l['points_ecartes'] for l in lignes)} écartés : illisibles ou sans réponse)"
             + (", réponses « sûres » seulement" if args.sures_seulement else "") + ".", "",
             "| Catégorie de la carte | Points | Pertes réellement vues | IC 95 % (Wilson) |",
             "| --- | --- | --- | --- |"]
    for l in lignes:
        ic = "—" if l["ic95"] is None else f"{pct(l['ic95'][0])} à {pct(l['ic95'][1])}"
        texte.append(f"| {l['nom']} | {l['points_valides']} | {l['pertes_vues']} ({pct(l['part_pertes'])}) | {ic} |")
    conf = next(l for l in lignes if l["strate"] == "perte_confirmee")
    stab = next(l for l in lignes if l["strate"] == "stable")
    texte += ["",
              f"- **Précision des pertes confirmées** : {pct(conf['part_pertes'])} des points « perte confirmée » "
              "montrent une vraie perte de végétation.",
              f"- **Pertes manquées dans le « stable »** : {pct(stab['part_pertes'])} des points stables montrent "
              "une perte.",
              f"- **Surface de pertes réelles estimée** (Olofsson et al., 2014) : "
              f"{ha(est['hectares_pertes_reelles'])} ha (IC 95 % : {ha(est['ic95_hectares'][0])} à "
              f"{ha(est['ic95_hectares'][1])} ha), sur les {ha(est['surface_echantillonnee_ha'])} ha des quatre "
              "catégories échantillonnées.",
              f"- **Part de ces pertes captée par la catégorie « perte confirmée »** : "
              f"{pct(est['part_captee_par_pertes_confirmees'])}.", "",
              "Lecture : si la précision des pertes confirmées est nettement plus élevée que celle des pertes non "
              "confirmées, la confirmation par le NDVI apporte bien de l'information. Le contrôle est visuel, sur "
              "images Sentinel-2 à 10 m (et Google Earth si utilisé) : il ne remplace pas le terrain."]
    sortie = dossier / "resultats_validation.md"
    sortie.write_text("\n".join(texte), encoding="utf-8")
    with open(dossier / "resultats_validation.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["categorie", "points_valides", "points_ecartes", "pertes_vues", "gains_vus", "stables_vus",
                    "eau_vue", "part_pertes", "ic95_bas", "ic95_haut", "poids_surface", "hectares_carte"])
        for l in lignes:
            ic = l["ic95"] or (None, None)
            w.writerow([l["nom"], l["points_valides"], l["points_ecartes"], l["pertes_vues"], l["gains_vus"],
                        l["stables_vus"], l["eau_vue"], l["part_pertes"], ic[0], ic[1], l["poids_surface"],
                        round(l["hectares_carte"], 2)])
    print("\n".join(texte))
    print(f"\nRésultats écrits dans {sortie}")


if __name__ == "__main__":
    main()
