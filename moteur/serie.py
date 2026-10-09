"""
Suivi temporel : détection de la dégradation des terres à partir de
plusieurs dates de la même zone et de la même saison.

Cadre : indicateur ODD 15.3.1 de la CNULCD (UNCCD), qui évalue la
dégradation par des sous-indicateurs combinés selon la règle
« un dehors, tous dehors » (one out, all out). Le système en calcule deux :

  1. Occupation du sol  : cartes du U-Net, état de début comparé à l'état
                          de fin (comparaison post-classification) ;
  2. Productivité       : tendance du NDVI de chaque pixel (Mann-Kendall,
                          pente de Sen), mesurée directement sur les images,
                          donc indépendante des erreurs du modèle.

Contrôles complémentaires :
  - évolution progressive : tendance de Mann-Kendall du rang de couverture
    végétale de chaque pixel (dégradation qui se confirme d'année en année) ;
  - terre ferme : statistiques recalculées sans les pixels qui ont été de
    l'eau au moins une fois (le niveau du lac masque sinon le reste) ;
  - terres nues : sol nu + sable/dune regroupés, ces deux classes étant
    celles que le modèle confond le plus.

Seuil unique : niveau de signification 0,05 des tests.
"""
import csv
import json
import math
from datetime import date
from pathlib import Path
from statistics import NormalDist

import numpy as np

from moteur.config_modele import CLASSES, NON_CLASSE
from moteur.resultats import HECTARES_PAR_PIXEL, _reduire

VERSION = 2


class ErreurSerie(Exception):
    """Erreur compréhensible par l'utilisateur."""


# ---------------------------------------------------------------------
# Règles (hypothèses du projet, à citer comme telles)
# ---------------------------------------------------------------------
# Rang de chaque classe terrestre sur un gradient de couverture végétale.
# Passer à un rang plus élevé = perte de couverture végétale.
RANG = {1: 0,    # Végétation saine
        2: 1,    # Végétation clairsemée ou dégradée
        3: 2,    # Sol nu
        4: 3}    # Sable / Dune
EAU = 0
STABLE, DEGRADATION, AMELIORATION, HYDRIQUE, CONFIRMEE, INDETERMINE = 0, 1, 2, 3, 4, 255
NIVEAU_SIGNIFICATION = 0.05
TAILLE_PERIODE = 3               # dates regroupées au début et à la fin
MIN_DATES_PERIODES = 6           # en dessous : première date contre dernière
MIN_DATES_TENDANCE = 5           # en dessous, aucun test exact ne peut être significatif
LIGNES_PAR_BLOC = 128
SANS_DONNEE_NDVI = -32768

# Cartes produites : nom de fichier -> (titre, catégories (code, nom, couleur))
CARTES = {
    "synthese": ("Synthèse (règle UNCCD « un dehors, tous dehors »)", [
        (CONFIRMEE, "Dégradation confirmée par les deux indicateurs", "#7b1d12"),
        (DEGRADATION, "Dégradation signalée par un seul indicateur", "#e0603f"),
        (AMELIORATION, "Amélioration", "#1e8449"),
        (STABLE, "Stable", "#c9cfc9")]),
    "occupation": ("Occupation du sol : début comparé à la fin", [
        (DEGRADATION, "Perte de couverture végétale", "#c0392b"),
        (AMELIORATION, "Gain de couverture végétale", "#1e8449"),
        (HYDRIQUE, "Changement lié à l'eau", "#2e86c1"),
        (STABLE, "Stable", "#c9cfc9")]),
    "progression": ("Évolution progressive (tendance année par année)", [
        (DEGRADATION, "Perte progressive significative", "#c0392b"),
        (AMELIORATION, "Gain progressif significatif", "#1e8449"),
        (STABLE, "Pas de tendance significative", "#c9cfc9")]),
    "productivite": ("Productivité : tendance du NDVI", [
        (DEGRADATION, "Baisse significative du NDVI", "#c0392b"),
        (AMELIORATION, "Hausse significative du NDVI", "#1e8449"),
        (STABLE, "Pas de tendance significative", "#c9cfc9")]),
}


# ---------------------------------------------------------------------
# Statistiques : Mann-Kendall et pente de Sen
# ---------------------------------------------------------------------
def _loi_exacte_s(n):
    """Nombre de permutations de n valeurs pour chaque nombre d'inversions
    (loi exacte de S quand il n'y a pas d'ex æquo)."""
    comptes = [1]
    for k in range(2, n + 1):
        nouveau = [0] * (len(comptes) + k - 1)
        for i, v in enumerate(comptes):
            for j in range(k):
                nouveau[i + j] += v
        comptes = nouveau
    return comptes


def p_exacte(n, s):
    comptes = _loi_exacte_s(n)
    paires = n * (n - 1) // 2
    return sum(c for inv, c in enumerate(comptes) if abs(paires - 2 * inv) >= abs(s)) / sum(comptes)


def seuil_s_exact(n, niveau=NIVEAU_SIGNIFICATION):
    """Plus petite valeur de |S| significative au niveau donné (loi exacte)."""
    paires = n * (n - 1) // 2
    for s in range(paires % 2, paires + 1, 2):
        if p_exacte(n, s) < niveau:
            return s
    return paires + 1                 # jamais significatif (n trop petit)


def mann_kendall(valeurs):
    """Test de tendance monotone de Mann-Kendall (bilatéral) sur une série."""
    x = list(valeurs)
    n = len(x)
    if n < 3:
        return {"n": n, "s": None, "tau": None, "p": None, "methode": "trop peu de dates"}
    s = 0
    for i in range(n - 1):
        for j in range(i + 1, n):
            s += (x[j] > x[i]) - (x[j] < x[i])
    paires = n * (n - 1) // 2
    if len(set(x)) == n:
        p, methode = p_exacte(n, s), "exacte"
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
    return {"n": n, "s": s, "tau": round(s / paires, 3), "p": round(p, 6), "methode": methode}


def pente_sen(temps, valeurs):
    """Pente de Sen : médiane des pentes entre toutes les paires de dates."""
    pentes = sorted((valeurs[j] - valeurs[i]) / (temps[j] - temps[i])
                    for i in range(len(temps) - 1) for j in range(i + 1, len(temps))
                    if temps[j] != temps[i])
    if not pentes:
        return None
    m = len(pentes) // 2
    return pentes[m] if len(pentes) % 2 else (pentes[m - 1] + pentes[m]) / 2


def s_par_pixel(x):
    """Statistique S de Mann-Kendall pour chaque pixel. x : (n, m)."""
    n = x.shape[0]
    s = np.zeros(x.shape[1], dtype=np.int16)
    for i in range(n - 1):
        for j in range(i + 1, n):
            s += np.sign(x[j].astype(np.int32) - x[i]).astype(np.int16)
    return s


def tendance_rangs(rangs, niveau=NIVEAU_SIGNIFICATION):
    """Tendance de Mann-Kendall d'une suite de rangs (beaucoup d'ex æquo) :
    approximation normale avec correction des ex æquo.
    Renvoie +1 (hausse significative), -1 (baisse) ou 0, pour chaque pixel."""
    n = rangs.shape[0]
    s = s_par_pixel(rangs).astype(np.float64)
    ex = np.zeros(rangs.shape[1], dtype=np.float64)
    for v in range(4):
        t = (rangs == v).sum(axis=0).astype(np.float64)
        ex += t * (t - 1) * (2 * t + 5)
    variance = (n * (n - 1) * (2 * n + 5) - ex) / 18
    z = np.zeros_like(s)
    ok = variance > 0
    z[ok] = (s[ok] - np.sign(s[ok])) / np.sqrt(variance[ok])
    critique = NormalDist().inv_cdf(1 - niveau / 2)
    return np.where(z > critique, 1, np.where(z < -critique, -1, 0)).astype(np.int8)


def tendance_continue(x, temps, seuil_s):
    """Pour des valeurs continues (NDVI) : signe de la tendance significative
    (loi exacte de S) et pente de Sen, pour chaque pixel. x : (n, m)."""
    s = s_par_pixel(x)
    signe = np.where(s >= seuil_s, 1, np.where(s <= -seuil_s, -1, 0)).astype(np.int8)
    n = x.shape[0]
    pentes = np.stack([(x[j].astype(np.float32) - x[i]) / np.float32(temps[j] - temps[i])
                       for i in range(n - 1) for j in range(i + 1, n)])
    return signe, np.median(pentes, axis=0).astype(np.float32)


def annee_decimale(date_iso):
    d = date.fromisoformat(date_iso)
    debut, fin = date(d.year, 1, 1), date(d.year + 1, 1, 1)
    return d.year + (d - debut).days / (fin - debut).days


# ---------------------------------------------------------------------
# Règles de changement
# ---------------------------------------------------------------------
def _table_rangs():
    t = np.full(256, -1, dtype=np.int16)
    for classe, r in RANG.items():
        t[classe] = r
    return t


TABLE_RANGS = _table_rangs()


def categorie_changement(debut, fin):
    """Occupation du sol : catégorie de changement entre deux cartes."""
    resultat = np.full(debut.shape, INDETERMINE, dtype=np.uint8)
    connu = (debut != NON_CLASSE) & (fin != NON_CLASSE)
    resultat[connu] = STABLE
    resultat[connu & ((debut == EAU) ^ (fin == EAU))] = HYDRIQUE
    rd, rf = TABLE_RANGS[debut], TABLE_RANGS[fin]
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


def combiner(occupation, productivite):
    """Règle « un dehors, tous dehors » sur deux indicateurs (codes STABLE,
    DEGRADATION, AMELIORATION ; INDETERMINE si l'un manque)."""
    resultat = np.full(occupation.shape, INDETERMINE, dtype=np.uint8)
    connu = (occupation != INDETERMINE) & (productivite != INDETERMINE)
    o, p = occupation, productivite
    deg_o, deg_p = o == DEGRADATION, p == DEGRADATION
    resultat[connu] = STABLE
    resultat[connu & ((o == AMELIORATION) | (p == AMELIORATION))] = AMELIORATION
    resultat[connu & (deg_o | deg_p)] = DEGRADATION
    resultat[connu & deg_o & deg_p] = CONFIRMEE
    return resultat


# ---------------------------------------------------------------------
# Lecture et écriture des rasters (isolées pour pouvoir les tester)
# ---------------------------------------------------------------------
def _ouvrir(chemin):
    import rasterio
    return rasterio.open(chemin)


def _lire_lignes(src, r0, r1):
    return src.read(1, window=((r0, r1), (0, src.width)))


def _creer(chemin, modele, dtype, nodata, couleurs=None):
    import rasterio
    profil = {"driver": "GTiff", "height": modele.height, "width": modele.width,
              "count": 1, "dtype": dtype, "crs": modele.crs, "transform": modele.transform,
              "nodata": nodata, "compress": "deflate"}
    dst = rasterio.open(chemin, "w", **profil)
    if couleurs:
        dst.write_colormap(1, couleurs)
    return dst


def _ecrire_lignes(dst, bloc, r0):
    dst.write(bloc, 1, window=((r0, r0 + bloc.shape[0]), (0, bloc.shape[1])))


def _emprise_wgs84(src):
    from rasterio.warp import transform_bounds
    return transform_bounds(src.crs, "EPSG:4326", *src.bounds)


def _rvb(hexa):
    return tuple(int(hexa[i:i + 2], 16) for i in (1, 3, 5))


def ecrire_png(carte, categories, chemin, cote_max=4096):
    """PNG pour la carte web ; stable et indéterminé transparents."""
    from PIL import Image
    petit = _reduire(carte, cote_max)
    image = np.zeros(petit.shape + (4,), dtype=np.uint8)
    for code, _, couleur in categories:
        if code != STABLE:
            image[petit == code] = _rvb(couleur) + (255,)
    Image.fromarray(image).save(chemin)


def _mediane_histogramme(h):
    """Médiane (NDVI x 10000) à partir d'un histogramme décalé de 10000."""
    total = h.sum()
    if total == 0:
        return None
    cumul = np.cumsum(h)
    return int(np.searchsorted(cumul, (total + 1) / 2)) - 10000


# ---------------------------------------------------------------------
# Chaîne complète
# ---------------------------------------------------------------------
def analyser_serie(entrees, dossier_sortie, etape=None):
    """
    entrees : liste de (date "AAAA-MM-JJ", chemin classes.tif, chemin ndvi.tif ou None).
    Écrit les cartes (GeoTIFF + PNG), serie.csv, transitions.csv et resume.json.
    """
    signaler = etape or (lambda texte, part: None)
    entrees = sorted(entrees)
    dates = [e[0] for e in entrees]
    n = len(entrees)
    if n < 2:
        raise ErreurSerie("Il faut au moins deux analyses pour un suivi.")
    if len(set(dates)) < n:
        raise ErreurSerie("Deux analyses portent sur la même date : gardez-en une seule par date.")
    avec_ndvi = all(e[2] for e in entrees)
    par_pixel = n >= MIN_DATES_TENDANCE
    dossier = Path(dossier_sortie)
    dossier.mkdir(parents=True, exist_ok=True)

    nb = len(CLASSES)
    k = TAILLE_PERIODE if n >= MIN_DATES_PERIODES else 1
    temps = [annee_decimale(d) for d in dates]
    seuil_s = seuil_s_exact(n)

    sources, sources_ndvi, sorties, sortie_pente = [], [], {}, None
    try:
        sources = [_ouvrir(e[1]) for e in entrees]
        sources_ndvi = [_ouvrir(e[2]) for e in entrees] if avec_ndvi else []
        ref = sources[0]
        for src in sources[1:] + sources_ndvi:
            if (src.height, src.width) != (ref.height, ref.width) or src.transform != ref.transform:
                raise ErreurSerie(
                    "Les cartes ne couvrent pas exactement la même zone : les analyses "
                    "doivent avoir été lancées sur la même zone.")
        H, L = ref.height, ref.width

        comptes_commun = np.zeros((n, nb), dtype=np.int64)
        comptes_terre = np.zeros((n, nb), dtype=np.int64)
        transitions = np.zeros(nb * nb, dtype=np.int64)
        histo_ndvi = np.zeros((n, 20001), dtype=np.int64)
        cartes = {nom: np.full((H, L), INDETERMINE, dtype=np.uint8) for nom in CARTES}
        somme_pente, nb_pente = 0.0, 0
        for nom, (_, cats) in CARTES.items():
            sorties[nom] = _creer(dossier / f"{nom}.tif", ref, "uint8", INDETERMINE,
                                  {code: _rvb(c) + (255,) for code, _, c in cats})
        if avec_ndvi and par_pixel:
            sortie_pente = _creer(dossier / "ndvi_pente.tif", ref, "float32", float("nan"))

        for r0 in range(0, H, LIGNES_PAR_BLOC):
            r1 = min(r0 + LIGNES_PAR_BLOC, H)
            signaler(f"Calcul des tendances : ligne {r0} sur {H}", 5 + int(85 * r0 / H))
            pile = np.stack([_lire_lignes(s, r0, r1) for s in sources])          # (n, h, L)
            commun = (pile != NON_CLASSE).all(axis=0)
            terre = commun & (pile != EAU).all(axis=0)
            for i in range(n):
                comptes_commun[i] += np.bincount(pile[i][commun], minlength=nb)[:nb]
                comptes_terre[i] += np.bincount(pile[i][terre], minlength=nb)[:nb]

            # 1. Occupation du sol : début contre fin
            debut = classe_majoritaire([pile[i] for i in range(k)])
            fin = classe_majoritaire([pile[i] for i in range(n - k, n)])
            debut[~commun] = NON_CLASSE
            fin[~commun] = NON_CLASSE
            occupation = categorie_changement(debut, fin)
            connu = occupation != INDETERMINE
            transitions += np.bincount(debut[connu].astype(np.int64) * nb + fin[connu],
                                       minlength=nb * nb)
            cartes["occupation"][r0:r1] = occupation

            # 2. Évolution progressive du rang de couverture (terre ferme)
            progression = np.full(terre.shape, INDETERMINE, dtype=np.uint8)
            if par_pixel and terre.any():
                signe = tendance_rangs(TABLE_RANGS[pile[:, terre]].astype(np.int8))
                progression[terre] = np.where(signe > 0, DEGRADATION,
                                              np.where(signe < 0, AMELIORATION, STABLE))
            cartes["progression"][r0:r1] = progression

            # 3. Productivité : tendance du NDVI (terre ferme)
            productivite = np.full(terre.shape, INDETERMINE, dtype=np.uint8)
            if avec_ndvi:
                ndvi = np.stack([_lire_lignes(s, r0, r1) for s in sources_ndvi])
                valide = terre & (ndvi != SANS_DONNEE_NDVI).all(axis=0)
                for i in range(n):
                    histo_ndvi[i] += np.bincount(
                        ndvi[i][valide].astype(np.int64) + 10000, minlength=20001)[:20001]
                pente_bloc = np.full(terre.shape, np.nan, dtype=np.float32)
                if par_pixel and valide.any():
                    signe, pente = tendance_continue(ndvi[:, valide], temps, seuil_s)
                    productivite[valide] = np.where(signe < 0, DEGRADATION,
                                                    np.where(signe > 0, AMELIORATION, STABLE))
                    pente_bloc[valide] = pente / 10000.0
                    somme_pente += float(pente.astype(np.float64).sum()) / 10000.0
                    nb_pente += int(valide.sum())
                if sortie_pente is not None:
                    _ecrire_lignes(sortie_pente, pente_bloc, r0)
            cartes["productivite"][r0:r1] = productivite

            # 4. Synthèse : occupation (terre ferme) et productivité
            occupation_terre = np.where(terre, occupation, INDETERMINE).astype(np.uint8)
            cartes["synthese"][r0:r1] = combiner(occupation_terre, productivite)

            for nom in CARTES:
                _ecrire_lignes(sorties[nom], cartes[nom][r0:r1], r0)

        emprise = _emprise_wgs84(ref)
    finally:
        for s in sources + sources_ndvi + list(sorties.values()):
            s.close()
        if sortie_pente is not None:
            sortie_pente.close()

    signaler("Statistiques et images", 92)
    if comptes_commun[0].sum() == 0:
        raise ErreurSerie("Aucun pixel n'est classé à toutes les dates.")

    # --- Séries de surfaces (toute la zone et terre ferme), avec terres nues ---
    noms_series = list(CLASSES) + ["Terres nues (sol nu + sable/dune)"]

    def serie(comptes):
        total_ha = int(comptes[0].sum()) * HECTARES_PAR_PIXEL
        lignes = []
        for i, d in enumerate(dates):
            ha = [round(int(c) * HECTARES_PAR_PIXEL, 2) for c in comptes[i]]
            ha.append(round(ha[3] + ha[4], 2))
            lignes.append({"date": d, "hectares": ha,
                           "pourcentages": [round(100 * v / total_ha, 2) if total_ha else 0.0
                                            for v in ha]})
        return lignes, round(total_ha, 2)

    def tendances(lignes, exclure_eau=False):
        resultat = []
        for c, nom in enumerate(noms_series):
            if exclure_eau and c == EAU:
                resultat.append(None)
                continue
            valeurs = [l["hectares"][c] for l in lignes]
            mk = mann_kendall(valeurs)
            pente = pente_sen(temps, valeurs)
            sig = mk["p"] is not None and mk["p"] < NIVEAU_SIGNIFICATION
            resultat.append({
                "classe": nom, **mk,
                "pente_ha_par_an": None if pente is None else round(pente, 1),
                "variation_ha": round(valeurs[-1] - valeurs[0], 2),
                "sens": ("hausse significative" if sig and mk["s"] > 0 else
                         "baisse significative" if sig else "pas de tendance significative"),
            })
        return resultat

    serie_zone, ha_commun = serie(comptes_commun)
    serie_terre, ha_terre = serie(comptes_terre)

    # --- Bilan et image de chaque carte ---
    bilans = {}
    for nom, (titre, categories) in CARTES.items():
        carte = cartes[nom]
        valeurs = np.bincount(carte.ravel(), minlength=256)
        total = int(sum(valeurs[code] for code, _, _ in categories))
        bilans[nom] = {
            "titre": titre,
            "disponible": total > 0,
            "hectares_compares": round(total * HECTARES_PAR_PIXEL, 2),
            "categories": [{
                "code": code, "nom": libelle, "couleur": couleur,
                "hectares": round(int(valeurs[code]) * HECTARES_PAR_PIXEL, 2),
                "pourcentage": round(100.0 * int(valeurs[code]) / total, 2) if total else 0.0,
            } for code, libelle, couleur in categories],
        }
        if total:
            ecrire_png(carte, categories, dossier / f"{nom}.png")

    # Persistance : part des pertes début/fin qui sont aussi progressives
    pertes = cartes["occupation"] == DEGRADATION
    nb_pertes = int(pertes.sum())
    persistance = (round(100.0 * int((pertes & (cartes["progression"] == DEGRADATION)).sum())
                         / nb_pertes, 1) if nb_pertes and par_pixel else None)

    # --- NDVI médian par date (terre ferme) et sa tendance ---
    ndvi_dates = None
    if avec_ndvi:
        medianes = [_mediane_histogramme(histo_ndvi[i]) for i in range(n)]
        if all(m is not None for m in medianes):
            valeurs = [m / 10000.0 for m in medianes]
            mk = mann_kendall(valeurs)
            pente = pente_sen(temps, valeurs)
            ndvi_dates = {"medianes": valeurs, **mk,
                          "pente_par_an": None if pente is None else round(pente, 5),
                          "pente_moyenne_pixels": round(somme_pente / nb_pente, 5) if nb_pente else None}

    # --- Fichiers CSV ---
    with open(dossier / "serie.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["date"] + [f"{c} (ha) - zone" for c in noms_series]
                   + [f"{c} (ha) - terre ferme" for c in noms_series[1:]]
                   + (["NDVI median - terre ferme"] if ndvi_dates else []))
        for i, (a, b) in enumerate(zip(serie_zone, serie_terre)):
            w.writerow([a["date"]] + a["hectares"] + b["hectares"][1:]
                       + ([ndvi_dates["medianes"][i]] if ndvi_dates else []))
    matrice = [[round(int(transitions[a * nb + b]) * HECTARES_PAR_PIXEL, 2) for b in range(nb)]
               for a in range(nb)]
    with open(dossier / "transitions.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["debut \\ fin (ha)"] + list(CLASSES))
        for nom, ligne in zip(CLASSES, matrice):
            w.writerow([nom] + ligne)

    # --- Avertissements ---
    mois = sorted({d[5:7] for d in dates})
    avertissements = []
    if len(mois) > 1:
        avertissements.append(
            "Les images ne sont pas toutes du même mois (" + ", ".join(mois) + ") : une partie "
            "des différences peut venir de la saison et non d'une évolution.")
    if n < 10:
        avertissements.append(f"Série de {n} dates : les tests de tendance ont peu de puissance.")
    if not par_pixel:
        avertissements.append(f"Moins de {MIN_DATES_TENDANCE} dates : pas de tendance par pixel.")
    if not avec_ndvi:
        avertissements.append("NDVI indisponible pour au moins une date : la productivité et "
                              "la synthèse n'ont pas pu être calculées.")

    resume = {
        "version": VERSION,
        "dates": dates, "nb_dates": n,
        "classes": noms_series,
        "emprise_wgs84": [round(v, 6) for v in emprise],
        "hectares_communs": ha_commun,
        "part_commune": round(100.0 * ha_commun / (H * L * HECTARES_PAR_PIXEL), 2),
        "hectares_terre": ha_terre,
        "serie": serie_zone, "serie_terre": serie_terre,
        "tendances": tendances(serie_zone),
        "tendances_terre": tendances(serie_terre, exclure_eau=True),
        "niveau_signification": NIVEAU_SIGNIFICATION,
        "seuil_s_pixel": seuil_s,
        "periode_debut": dates[:k], "periode_fin": dates[n - k:],
        "bilans": bilans,
        "persistance": persistance,
        "ndvi": ndvi_dates,
        "transitions": matrice,
        "avertissements": avertissements,
    }
    with open(dossier / "resume.json", "w", encoding="utf-8") as f:
        json.dump(resume, f, ensure_ascii=False, indent=2)
    return resume
