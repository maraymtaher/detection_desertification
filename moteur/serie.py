"""
Suivi temporel : comparaison de plusieurs cartes d'occupation du sol
de la même zone, à des dates différentes.

Principe (comparaison post-classification) :
  1. on ne garde que les pixels classés à TOUTES les dates, pour que les
     surfaces soient comparables d'une date à l'autre ;
  2. on mesure la surface de chaque classe à chaque date ;
  3. on teste la tendance de chaque classe (Mann-Kendall, pente de Sen) ;
  4. on compare l'état de début et l'état de fin, pixel par pixel.

Aucun seuil numérique n'intervient : les seules règles sont l'ordre des
classes (REGLE ci-dessous) et le niveau de signification du test.
"""
import csv
import json
import math
from datetime import date
from pathlib import Path

import numpy as np

from moteur.config_modele import CLASSES, NON_CLASSE
from moteur.resultats import HECTARES_PAR_PIXEL, _reduire


class ErreurSerie(Exception):
    """Erreur compréhensible par l'utilisateur."""


# ---------------------------------------------------------------------
# Règle de lecture des changements (hypothèse du projet, à citer comme telle)
# ---------------------------------------------------------------------
# Rang de chaque classe terrestre sur un gradient de couverture végétale.
# Passer à un rang plus élevé = perte de couverture végétale.
RANG = {1: 0,    # Végétation saine
        2: 1,    # Végétation clairsemée ou dégradée
        3: 2,    # Sol nu
        4: 3}    # Sable / Dune
EAU = 0
# Codes de la carte de changement
STABLE, DEGRADATION, AMELIORATION, HYDRIQUE, INDETERMINE = 0, 1, 2, 3, 255
CATEGORIES = [
    ("Stable", "#c9cfc9"),
    ("Dégradation (perte de couverture végétale)", "#c0392b"),
    ("Amélioration (gain de couverture végétale)", "#1e8449"),
    ("Changement lié à l'eau", "#2e86c1"),
]
NIVEAU_SIGNIFICATION = 0.05      # convention statistique usuelle
TAILLE_PERIODE = 3               # dates regroupées au début et à la fin
MIN_DATES_PERIODES = 6           # en dessous : première date contre dernière


def categorie_changement(debut, fin):
    """Carte de changement à partir de deux cartes de classes."""
    resultat = np.full(debut.shape, INDETERMINE, dtype=np.uint8)
    connu = (debut != NON_CLASSE) & (fin != NON_CLASSE)
    resultat[connu] = STABLE
    eau = connu & ((debut == EAU) ^ (fin == EAU))
    resultat[eau] = HYDRIQUE
    rang = np.full(256, -1, dtype=np.int16)
    for classe, r in RANG.items():
        rang[classe] = r
    rd, rf = rang[debut], rang[fin]
    terre = connu & (rd >= 0) & (rf >= 0)
    resultat[terre & (rf > rd)] = DEGRADATION
    resultat[terre & (rf < rd)] = AMELIORATION
    return resultat


def classe_majoritaire(cartes):
    """Classe présente dans la majorité des cartes (1 ou 3 cartes).
    Avec 3 cartes, un pixel dont les trois classes diffèrent est indéterminé."""
    if len(cartes) == 1:
        return cartes[0].copy()
    a, b, c = cartes
    resultat = np.full(a.shape, NON_CLASSE, dtype=np.uint8)
    resultat[b == c] = b[b == c]
    resultat[a == c] = a[a == c]
    resultat[a == b] = a[a == b]
    return resultat


# ---------------------------------------------------------------------
# Tendance : Mann-Kendall et pente de Sen
# ---------------------------------------------------------------------
def _loi_exacte_s(n):
    """Nombre de permutations de n valeurs pour chaque nombre d'inversions
    (loi exacte de la statistique S quand il n'y a pas d'ex æquo)."""
    comptes = [1]
    for k in range(2, n + 1):
        nouveau = [0] * (len(comptes) + k - 1)
        for i, v in enumerate(comptes):
            for j in range(k):
                nouveau[i + j] += v
        comptes = nouveau
    return comptes


def mann_kendall(valeurs):
    """Test de tendance monotone de Mann-Kendall (bilatéral).
    Renvoie S, tau, p et la méthode de calcul de p :
      - « exacte » sans ex æquo (toutes les permutations) ;
      - « approximation normale » avec correction des ex æquo sinon."""
    x = list(valeurs)
    n = len(x)
    if n < 3:
        return {"n": n, "s": None, "tau": None, "p": None, "methode": "trop peu de dates"}
    s = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            s += (x[j] > x[i]) - (x[j] < x[i])
    paires = n * (n - 1) // 2
    exaequo = len(set(x)) < n
    if not exaequo:
        comptes = _loi_exacte_s(n)
        total = sum(comptes)
        # S = paires - 2 * inversions
        favorables = sum(c for inv, c in enumerate(comptes)
                         if abs(paires - 2 * inv) >= abs(s))
        p, methode = favorables / total, "exacte"
    else:
        groupes = {}
        for v in x:
            groupes[v] = groupes.get(v, 0) + 1
        variance = (n * (n - 1) * (2 * n + 5)
                    - sum(t * (t - 1) * (2 * t + 5) for t in groupes.values())) / 18
        if variance <= 0:
            p = 1.0
        else:
            z = (s - (s > 0) + (s < 0)) / math.sqrt(variance)
            p = math.erfc(abs(z) / math.sqrt(2))
        methode = "approximation normale"
    return {"n": n, "s": s, "tau": round(s / paires, 3), "p": round(p, 6),
            "methode": methode}


def pente_sen(temps, valeurs):
    """Pente de Sen : médiane des pentes entre toutes les paires de dates."""
    pentes = [(valeurs[j] - valeurs[i]) / (temps[j] - temps[i])
              for i in range(len(temps) - 1) for j in range(i + 1, len(temps))
              if temps[j] != temps[i]]
    if not pentes:
        return None
    pentes.sort()
    m = len(pentes) // 2
    return pentes[m] if len(pentes) % 2 else (pentes[m - 1] + pentes[m]) / 2


def annee_decimale(date_iso):
    d = date.fromisoformat(date_iso)
    debut, fin = date(d.year, 1, 1), date(d.year + 1, 1, 1)
    return d.year + (d - debut).days / (fin - debut).days


# ---------------------------------------------------------------------
# Lecture et écriture des cartes
# ---------------------------------------------------------------------
def lire_classes(chemin):
    """Carte des classes d'une analyse : (tableau, transform, crs)."""
    import rasterio
    with rasterio.open(chemin) as src:
        return src.read(1), src.transform, src.crs


def _ecrire_geotiff(carte, chemin, transform, crs):
    import rasterio
    profil = {"driver": "GTiff", "height": carte.shape[0], "width": carte.shape[1],
              "count": 1, "dtype": "uint8", "crs": crs, "transform": transform,
              "nodata": INDETERMINE, "compress": "deflate"}
    with rasterio.open(chemin, "w", **profil) as dst:
        dst.write(carte, 1)
        dst.write_colormap(1, {i: _rvb(h) + (255,) for i, (_, h) in enumerate(CATEGORIES)})


def _emprise_wgs84(forme, transform, crs):
    from moteur.resultats import emprise_wgs84
    return emprise_wgs84(forme, transform, crs)


def _rvb(hexa):
    return tuple(int(hexa[i:i + 2], 16) for i in (1, 3, 5))


def ecrire_png_changement(changement, chemin, cote_max=4096):
    """PNG pour la carte web. Stable et indéterminé sont transparents :
    seuls les pixels qui ont changé sont colorés."""
    from PIL import Image
    petit = _reduire(changement, cote_max)
    image = np.zeros(petit.shape + (4,), dtype=np.uint8)
    for code in (DEGRADATION, AMELIORATION, HYDRIQUE):
        image[petit == code] = _rvb(CATEGORIES[code][1]) + (255,)
    Image.fromarray(image).save(chemin)


# ---------------------------------------------------------------------
# Chaîne complète
# ---------------------------------------------------------------------
def analyser_serie(entrees, dossier_sortie, etape=None):
    """
    entrees : liste de (date "AAAA-MM-JJ", chemin de classes.tif), au moins 2.
    Écrit dans dossier_sortie : changement.png, changement.tif, serie.csv,
    transitions.csv, resume.json. Renvoie le résumé.
    """
    signaler = etape or (lambda texte, part: None)
    entrees = sorted(entrees)
    dates = [d for d, _ in entrees]
    n = len(entrees)
    if n < 2:
        raise ErreurSerie("Il faut au moins deux analyses pour un suivi.")
    if len(set(dates)) < n:
        raise ErreurSerie("Deux analyses portent sur la même date : "
                          "gardez-en une seule par date.")
    dossier = Path(dossier_sortie)
    dossier.mkdir(parents=True, exist_ok=True)

    k = TAILLE_PERIODE if n >= MIN_DATES_PERIODES else 1
    garder = set(range(k)) | set(range(n - k, n))     # cartes de début et de fin

    # --- Passe 1 : pixels classés à toutes les dates ---
    commun, reference, gardees = None, None, {}
    for i, (d, chemin) in enumerate(entrees):
        signaler(f"Lecture de la carte du {d}", int(40 * i / n))
        carte, transform, crs = lire_classes(chemin)
        if reference is None:
            reference = (carte.shape, transform, crs)
            commun = carte != NON_CLASSE
        else:
            if carte.shape != reference[0] or transform != reference[1]:
                raise ErreurSerie(
                    f"La carte du {d} ne couvre pas exactement la même zone "
                    f"que celle du {dates[0]} : les analyses doivent avoir été "
                    "lancées sur la même zone.")
            commun &= carte != NON_CLASSE
        if i in garder:
            gardees[i] = carte
    forme, transform, crs = reference
    nb_commun = int(commun.sum())
    if nb_commun == 0:
        raise ErreurSerie("Aucun pixel n'est classé à toutes les dates.")

    # --- Passe 2 : surface de chaque classe sur les pixels communs ---
    serie = []
    for i, (d, chemin) in enumerate(entrees):
        signaler(f"Surfaces au {d}", 40 + int(30 * i / n))
        carte = gardees[i] if i in gardees else lire_classes(chemin)[0]
        comptes = np.bincount(carte[commun], minlength=256)
        serie.append({
            "date": d,
            "hectares": [round(int(comptes[c]) * HECTARES_PAR_PIXEL, 2)
                         for c in range(len(CLASSES))],
            "pourcentages": [round(100.0 * int(comptes[c]) / nb_commun, 2)
                             for c in range(len(CLASSES))],
        })

    # --- Tendance par classe ---
    temps = [annee_decimale(d) for d in dates]
    tendances = []
    for c, nom in enumerate(CLASSES):
        valeurs = [s["hectares"][c] for s in serie]
        mk = mann_kendall(valeurs)
        pente = pente_sen(temps, valeurs)
        significatif = mk["p"] is not None and mk["p"] < NIVEAU_SIGNIFICATION
        if not significatif:
            sens = "pas de tendance significative"
        else:
            sens = "hausse significative" if mk["s"] > 0 else "baisse significative"
        tendances.append({
            "classe": nom, **mk,
            "pente_ha_par_an": None if pente is None else round(pente, 1),
            "variation_ha": round(valeurs[-1] - valeurs[0], 2),
            "sens": sens,
        })

    # --- Changement entre le début et la fin ---
    signaler("Carte de changement", 75)
    debut = classe_majoritaire([gardees[i] for i in range(k)])
    fin = classe_majoritaire([gardees[i] for i in range(n - k, n)])
    debut[~commun] = NON_CLASSE
    fin[~commun] = NON_CLASSE
    changement = categorie_changement(debut, fin)
    connu = changement != INDETERMINE
    nb_connu = int(connu.sum())
    comptes = np.bincount(changement[connu], minlength=4)
    categories = [{
        "nom": nom, "couleur": couleur,
        "hectares": round(int(comptes[i]) * HECTARES_PAR_PIXEL, 2),
        "pourcentage": round(100.0 * int(comptes[i]) / nb_connu, 2) if nb_connu else 0.0,
    } for i, (nom, couleur) in enumerate(CATEGORIES)]

    nb = len(CLASSES)
    paires = np.bincount(debut[connu].astype(np.int64) * nb + fin[connu], minlength=nb * nb)
    transitions = [[round(int(paires[a * nb + b]) * HECTARES_PAR_PIXEL, 2)
                    for b in range(nb)] for a in range(nb)]

    signaler("Écriture des résultats", 90)
    ecrire_png_changement(changement, dossier / "changement.png")
    _ecrire_geotiff(changement, dossier / "changement.tif", transform, crs)
    with open(dossier / "serie.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["date"] + [f"{c} (ha)" for c in CLASSES])
        for s in serie:
            w.writerow([s["date"]] + s["hectares"])
    with open(dossier / "transitions.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["debut \\ fin (ha)"] + CLASSES)
        for nom, ligne in zip(CLASSES, transitions):
            w.writerow([nom] + ligne)

    mois = sorted({d[5:7] for d in dates})
    avertissements = []
    if len(mois) > 1:
        avertissements.append(
            "Les images ne sont pas toutes du même mois (" + ", ".join(mois) + ") : "
            "une partie des différences peut venir de la saison et non d'une évolution.")
    if n < 10:
        avertissements.append(
            f"Série de {n} dates : le test de tendance a peu de puissance avec "
            "aussi peu de dates.")

    resume = {
        "dates": dates,
        "nb_dates": n,
        "classes": list(CLASSES),
        "emprise_wgs84": [round(v, 6) for v in _emprise_wgs84(forme, transform, crs)],
        "hectares_communs": round(nb_commun * HECTARES_PAR_PIXEL, 2),
        "part_commune": round(100.0 * nb_commun / commun.size, 2),
        "serie": serie,
        "tendances": tendances,
        "niveau_signification": NIVEAU_SIGNIFICATION,
        "periode_debut": dates[:k],
        "periode_fin": dates[n - k:],
        "hectares_compares": round(nb_connu * HECTARES_PAR_PIXEL, 2),
        "categories": categories,
        "transitions": transitions,
        "avertissements": avertissements,
    }
    with open(dossier / "resume.json", "w", encoding="utf-8") as f:
        json.dump(resume, f, ensure_ascii=False, indent=2)
    return resume
