"""
Suivi temporel : détection automatique des changements d'occupation du sol
et de la dégradation des terres à partir de plusieurs dates d'une même zone
et d'une même saison (méthode version 3 = règle du mémoire, notebooks v7/v8).

Règle principale (carte de synthèse) :
  1. État dominant de chaque pixel sur la période de référence (3 premières
     dates) et sur la période récente (3 dernières) : eau, végétation
     (classes 1 + 2) ou surface nue (classes 3 + 4), retenu s'il apparaît
     au moins 2 fois sur 3 ; sinon le pixel est indéterminé.
     Avec moins de 6 dates : première date contre dernière.
  2. Perte candidate : végétation -> surface nue.
  3. Perte confirmée : perte candidate où le NDVI moyen de la période
     récente est inférieur de plus de 0,10 à celui de la période de
     référence, en taches d'au moins 50 pixels (0,5 ha, 4-connexité).
     Le NDVI est mesuré directement sur les images : il est indépendant
     des erreurs du modèle et sert de contrôle.
  4. Gain : surface nue -> végétation (gain >= 0,5 ha ou petit gain).
  5. Changement lié à l'eau : eau dans une seule des deux périodes.

Compléments (ne modifient pas la synthèse) :
  - évolution progressive du rang de couverture végétale (Mann-Kendall) ;
  - productivité : tendance du NDVI de chaque pixel (Mann-Kendall, Sen) ;
  - tendances des surfaces par classe (Mann-Kendall, pente de Sen) ;
  - domaine de validité : part de la zone située dans la zone
    d'entraînement du modèle (résultat « validé » ou « indicatif »).

Seuils : niveau de signification 0,05 ; baisse de NDVI 0,10 ; 0,5 ha.
"""
import csv
import json
import math
from datetime import date
from pathlib import Path
from statistics import NormalDist

import numpy as np

from moteur.config_modele import CLASSES, EMPRISE_ENTRAINEMENT, NON_CLASSE
from moteur.resultats import HECTARES_PAR_PIXEL, _reduire

VERSION = 3


class ErreurSerie(Exception):
    """Erreur compréhensible par l'utilisateur."""


# ---------------------------------------------------------------------
# Règles (hypothèses du projet, à citer comme telles)
# ---------------------------------------------------------------------
# Rang de chaque classe terrestre sur un gradient de couverture végétale.
# Passer à un rang plus élevé = perte de couverture végétale.
RANG = {1: 0,    # Végétation dense
        2: 1,    # Végétation clairsemée
        3: 2,    # Sol nu
        4: 3}    # Sable / Dune
EAU = 0
# Codes des cartes. Dans la synthèse : CONFIRMEE = perte confirmée,
# DEGRADATION = perte non confirmée, AMELIORATION = gain >= 0,5 ha.
STABLE, DEGRADATION, AMELIORATION, HYDRIQUE, CONFIRMEE, PETIT_GAIN, INDETERMINE = 0, 1, 2, 3, 4, 5, 255
NIVEAU_SIGNIFICATION = 0.05
TAILLE_PERIODE = 3               # dates regroupées au début et à la fin
MIN_DATES_PERIODES = 6           # en dessous : première date contre dernière
MIN_DATES_TENDANCE = 5           # en dessous, aucun test exact ne peut être significatif
LIGNES_PAR_BLOC = 128
SANS_DONNEE_NDVI = -32768

# Règle du mémoire (notebooks v7 / v8)
ETAT_EAU, ETAT_VEGETATION, ETAT_NU = 0, 1, 2
CLASSES_VEGETATION = (1, 2)      # dense + clairsemée
CLASSES_NU = (3, 4)              # sol nu + sable/dune
VOTES_MIN = 2                    # état retenu s'il apparaît 2 fois sur 3
SEUIL_BAISSE_NDVI = 0.10         # baisse du NDVI moyen de période
SEUIL_BAISSE_NDVI_X10000 = 1000  # même seuil, NDVI enregistré x 10000
TAILLE_MIN_PIXELS = 50           # 0,5 ha à 10 m
REGLE = {
    "nom": "Règle du mémoire (notebooks v7/v8)",
    "periodes": "3 premières dates contre 3 dernières (première contre dernière si moins de 6 dates)",
    "etat_dominant": "eau, végétation (classes 1+2) ou surface nue (classes 3+4), au moins 2 dates sur 3",
    "perte_candidate": "végétation -> surface nue",
    "perte_confirmee": "perte candidate + baisse du NDVI moyen de période > 0,10, taches >= 0,5 ha",
    "gain": "surface nue -> végétation, taches >= 0,5 ha",
    "seuil_ndvi": SEUIL_BAISSE_NDVI,
    "taille_min_ha": TAILLE_MIN_PIXELS * HECTARES_PAR_PIXEL,
}

# Cartes produites : nom de fichier -> (titre, catégories (code, nom, couleur))
# L'ordre des quatre premières catégories de la synthèse est utilisé par
# les pages (perte confirmée, perte non confirmée, gain, stable).
CARTES = {
    "synthese": ("Synthèse : changements détectés (U-Net + contrôle NDVI)", [
        (CONFIRMEE, "Perte de végétation confirmée (U-Net + NDVI, ≥ 0,5 ha)", "#b2182b"),
        (DEGRADATION, "Perte non confirmée par le NDVI", "#fdae61"),
        (AMELIORATION, "Gain de végétation (≥ 0,5 ha)", "#006d2c"),
        (STABLE, "Stable (eau, végétation ou surface nue)", "#c9cfc9"),
        (HYDRIQUE, "Changement lié à l'eau", "#2e86c1"),
        (PETIT_GAIN, "Petit gain (< 0,5 ha)", "#74c476")]),
    "occupation": ("Changement d'état : période de référence comparée à la période récente", [
        (DEGRADATION, "Végétation devenue surface nue (perte candidate)", "#c0392b"),
        (AMELIORATION, "Surface nue devenue végétation", "#1e8449"),
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


def etat_dominant(pile, votes_min=VOTES_MIN):
    """État dominant d'une période (notebook v8) : eau, végétation ou
    surface nue s'il apparaît au moins votes_min fois ; INDETERMINE si
    aucun état ou plusieurs états atteignent ce nombre. pile : (k, ...)."""
    eau = (pile == EAU).sum(axis=0)
    veg = np.isin(pile, CLASSES_VEGETATION).sum(axis=0)
    nu = np.isin(pile, CLASSES_NU).sum(axis=0)
    etat = np.full(pile.shape[1:], INDETERMINE, dtype=np.uint8)
    etat[eau >= votes_min] = ETAT_EAU
    etat[veg >= votes_min] = ETAT_VEGETATION
    etat[nu >= votes_min] = ETAT_NU
    ambigu = ((eau >= votes_min).astype(np.int8) + (veg >= votes_min) + (nu >= votes_min)) > 1
    etat[ambigu] = INDETERMINE
    return etat


def changement_etats(debut, fin):
    """Carte de changement entre les deux états dominants (codes de la
    carte « occupation ») : DEGRADATION = végétation -> surface nue,
    AMELIORATION = surface nue -> végétation, HYDRIQUE = eau dans une seule
    période, STABLE = même état, INDETERMINE sinon."""
    resultat = np.full(debut.shape, INDETERMINE, dtype=np.uint8)
    ok = (debut != INDETERMINE) & (fin != INDETERMINE)
    resultat[ok & (debut == fin)] = STABLE
    resultat[ok & (debut == ETAT_VEGETATION) & (fin == ETAT_NU)] = DEGRADATION
    resultat[ok & (debut == ETAT_NU) & (fin == ETAT_VEGETATION)] = AMELIORATION
    resultat[ok & ((debut == ETAT_EAU) != (fin == ETAT_EAU))] = HYDRIQUE
    return resultat


def ndvi_moyen_periode(ndvi, classes):
    """NDVI moyen (x 10000) des dates d'une période, en ignorant les valeurs
    absentes (nuage, hors image) et les pixels non classés. NaN si aucune."""
    valide = (ndvi != SANS_DONNEE_NDVI) & (classes != NON_CLASSE)
    nb = valide.sum(axis=0)
    somme = np.where(valide, ndvi.astype(np.float32), 0).sum(axis=0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(nb > 0, somme / np.maximum(nb, 1), np.nan).astype(np.float32)


def filtrer_petites_taches(masque, taille_min=TAILLE_MIN_PIXELS):
    """Garde les taches (4-connexité) d'au moins taille_min pixels."""
    if not masque.any():
        return masque.copy()
    try:
        from scipy import ndimage
    except ImportError as e:
        raise ErreurSerie("Le module scipy est nécessaire : installez-le avec  pip install scipy") from e
    lignes = np.where(masque.any(axis=1))[0]
    colonnes = np.where(masque.any(axis=0))[0]
    l0, l1, c0, c1 = lignes[0], lignes[-1] + 1, colonnes[0], colonnes[-1] + 1
    etiquettes, n = ndimage.label(masque[l0:l1, c0:c1])
    # taille de chaque tache, comptée par blocs de lignes : un np.bincount sur toute
    # la carte convertirait les étiquettes en int64 (près de 1 Go pour une tuile entière)
    tailles = np.zeros(n + 1, dtype=np.int64)
    for r in range(0, etiquettes.shape[0], LIGNES_PAR_BLOC):
        tailles += np.bincount(etiquettes[r:r + LIGNES_PAR_BLOC].ravel(), minlength=n + 1)
    garder = tailles >= taille_min
    garder[0] = False
    resultat = np.zeros(masque.shape, dtype=bool)
    resultat[l0:l1, c0:c1] = garder[etiquettes]
    return resultat


def compter_codes(carte, longueur=256):
    """Nombre de pixels de chaque code d'une carte uint8, compté par blocs de lignes
    (même résultat que np.bincount(carte.ravel()), sans copie int64 de toute la carte)."""
    total = np.zeros(longueur, dtype=np.int64)
    for r in range(0, carte.shape[0], LIGNES_PAR_BLOC):
        total += np.bincount(carte[r:r + LIGNES_PAR_BLOC].ravel(), minlength=longueur)[:longueur]
    return total


def synthese_regle(occupation, baisse_ndvi, ndvi_disponible):
    """Carte de synthèse (codes 0-5 + INDETERMINE) à partir de la carte de
    changement d'état et du masque « baisse du NDVI > 0,10 »."""
    syn = np.full(occupation.shape, INDETERMINE, dtype=np.uint8)
    syn[occupation == STABLE] = STABLE
    syn[occupation == HYDRIQUE] = HYDRIQUE
    gain = occupation == AMELIORATION
    syn[gain] = PETIT_GAIN
    syn[filtrer_petites_taches(gain)] = AMELIORATION
    perte = occupation == DEGRADATION
    syn[perte] = DEGRADATION
    syn[filtrer_petites_taches(perte & baisse_ndvi & ndvi_disponible)] = CONFIRMEE
    return syn


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


def _fenetre_validite(src):
    """Lignes et colonnes (r0, r1, c0, c1) de la zone d'entraînement du
    modèle dans la grille de src, ou None si elles ne se recouvrent pas."""
    from rasterio.warp import transform_bounds
    xmin, ymin, xmax, ymax = transform_bounds("EPSG:4326", src.crs, *EMPRISE_ENTRAINEMENT)
    t = src.transform
    c0, c1 = sorted(((xmin - t.c) / t.a, (xmax - t.c) / t.a))
    r0, r1 = sorted(((ymax - t.f) / t.e, (ymin - t.f) / t.e))
    r0, r1 = max(0, int(round(r0))), min(src.height, int(round(r1)))
    c0, c1 = max(0, int(round(c0))), min(src.width, int(round(c1)))
    return (r0, r1, c0, c1) if r1 > r0 and c1 > c0 else None


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
# Contrôle de qualité des images
# ---------------------------------------------------------------------
ECART_JOURS_MAX = 30            # au-delà, la phénologie peut différer d'une année à l'autre
PART_NON_CLASSEE_MAX = 20.0     # % de pixels sans classe (nuages, hors image)
SEUIL_ATYPIQUE = 3.0            # écart robuste (en MAD normalisée) du NDVI médian
ECART_NDVI_MIN = 0.03           # et au moins 0,03 d'écart (évite les fausses alertes)


def controle_qualite(dates, parts_non_classees, medianes_ndvi=None):
    """Repère les images qui peuvent fausser la comparaison :
    - dates trop étalées dans la saison (écart > ECART_JOURS_MAX jours) ;
    - images avec beaucoup de pixels non classés (> PART_NON_CLASSEE_MAX %) ;
    - images atypiques : NDVI médian à plus de SEUIL_ATYPIQUE écarts robustes
      (médiane des écarts absolus x 1,4826) de la médiane des dates.
    Renvoie un résumé par date et la liste des avertissements."""
    jours = []
    for d in dates:
        x = date.fromisoformat(d)
        j = x.timetuple().tm_yday
        jours.append(j)
    # écart circulaire minimal (une saison peut chevaucher le 1er janvier)
    tri = sorted(jours)
    trous = [b - a for a, b in zip(tri, tri[1:])] + [tri[0] + 365 - tri[-1]]
    ecart = 365 - max(trous) if len(tri) > 1 else 0

    atypiques = [False] * len(dates)
    if medianes_ndvi and len(medianes_ndvi) >= 4:
        m = float(np.median(medianes_ndvi))
        mad = float(np.median([abs(v - m) for v in medianes_ndvi])) * 1.4826
        atypiques = [abs(v - m) > max(SEUIL_ATYPIQUE * mad, ECART_NDVI_MIN)
                     for v in medianes_ndvi]

    par_date = [{
        "date": d,
        "jour": j,
        "part_non_classee": round(p, 1),
        "ndvi_median": None if not medianes_ndvi else round(medianes_ndvi[i], 3),
        "atypique": atypiques[i],
        "nuageuse": p > PART_NON_CLASSEE_MAX,
    } for i, (d, j, p) in enumerate(zip(dates, jours, parts_non_classees))]

    avertissements = []
    if ecart > ECART_JOURS_MAX:
        avertissements.append(
            f"Les images s'étalent sur {ecart} jours dans la saison : la végétation peut "
            "être plus ou moins sèche selon la date, ce qui peut créer de faux changements.")
    nuageuses = [x["date"] for x in par_date if x["nuageuse"]]
    if nuageuses:
        avertissements.append(
            f"Plus de {PART_NON_CLASSEE_MAX:.0f} % de pixels non classés (nuages, hors image) pour : "
            + ", ".join(nuageuses) + ". Les pixels concernés sont ignorés.")
    bizarres = [x["date"] for x in par_date if x["atypique"]]
    if bizarres:
        avertissements.append(
            "Image(s) atypique(s) : le NDVI médian s'écarte nettement des autres dates ("
            + ", ".join(bizarres) + "). Vérifiez-les (brume, humidité, date) ; la règle "
            "« 2 dates sur 3 » limite leur effet.")
    return {"ecart_jours": ecart, "par_date": par_date, "avertissements": avertissements,
            "seuils": {"ecart_jours_max": ECART_JOURS_MAX,
                       "part_non_classee_max": PART_NON_CLASSEE_MAX,
                       "seuil_atypique": SEUIL_ATYPIQUE}}


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
        non_classes = np.zeros(n, dtype=np.int64)       # pixels sans classe, par date
        comptes_terre = np.zeros((n, nb), dtype=np.int64)
        transitions = np.zeros(nb * nb, dtype=np.int64)
        histo_ndvi = np.zeros((n, 20001), dtype=np.int64)
        stables = np.zeros(3, dtype=np.int64)          # eau, végétation, surface nue
        cartes = {nom: np.full((H, L), INDETERMINE, dtype=np.uint8) for nom in CARTES}
        baisse_ndvi = np.zeros((H, L), dtype=bool)     # NDVI moyen : baisse > 0,10
        ndvi_connu = np.zeros((H, L), dtype=bool)      # NDVI mesuré dans les deux périodes
        somme_pente, nb_pente = 0.0, 0
        votes = VOTES_MIN if k == TAILLE_PERIODE else 1
        for nom, (_, cats) in CARTES.items():
            sorties[nom] = _creer(dossier / f"{nom}.tif", ref, "uint8", INDETERMINE,
                                  {code: _rvb(c) + (255,) for code, _, c in cats})
        if avec_ndvi and par_pixel:
            sortie_pente = _creer(dossier / "ndvi_pente.tif", ref, "float32", float("nan"))

        for r0 in range(0, H, LIGNES_PAR_BLOC):
            r1 = min(r0 + LIGNES_PAR_BLOC, H)
            signaler(f"Détection des changements : ligne {r0} sur {H}", 5 + int(80 * r0 / H))
            pile = np.stack([_lire_lignes(s, r0, r1) for s in sources])          # (n, h, L)
            commun = (pile != NON_CLASSE).all(axis=0)
            terre = commun & (pile != EAU).all(axis=0)
            non_classes += (pile == NON_CLASSE).reshape(n, -1).sum(axis=1)
            for i in range(n):
                comptes_commun[i] += np.bincount(pile[i][commun], minlength=nb)[:nb]
                comptes_terre[i] += np.bincount(pile[i][terre], minlength=nb)[:nb]

            # 1. Changement d'état (règle du mémoire) : référence contre récente
            etat_debut = etat_dominant(pile[:k], votes)
            etat_fin = etat_dominant(pile[n - k:], votes)
            occupation = changement_etats(etat_debut, etat_fin)
            cartes["occupation"][r0:r1] = occupation
            for e in (ETAT_EAU, ETAT_VEGETATION, ETAT_NU):
                stables[e] += int(((occupation == STABLE) & (etat_debut == e)).sum())

            # Matrice des transitions entre les 5 classes (pixels classés partout)
            debut = classe_majoritaire([pile[i] for i in range(k)])
            fin = classe_majoritaire([pile[i] for i in range(n - k, n)])
            connu = commun & (debut != NON_CLASSE) & (fin != NON_CLASSE)
            transitions += np.bincount(debut[connu].astype(np.int64) * nb + fin[connu],
                                       minlength=nb * nb)

            # 2. Évolution progressive du rang de couverture (complément)
            progression = np.full(terre.shape, INDETERMINE, dtype=np.uint8)
            if par_pixel and terre.any():
                signe = tendance_rangs(TABLE_RANGS[pile[:, terre]].astype(np.int8))
                progression[terre] = np.where(signe > 0, DEGRADATION,
                                              np.where(signe < 0, AMELIORATION, STABLE))
            cartes["progression"][r0:r1] = progression

            # 3. NDVI : contrôle des pertes (moyennes de période) et productivité
            productivite = np.full(terre.shape, INDETERMINE, dtype=np.uint8)
            if avec_ndvi:
                ndvi = np.stack([_lire_lignes(s, r0, r1) for s in sources_ndvi])
                moyenne_debut = ndvi_moyen_periode(ndvi[:k], pile[:k])
                moyenne_fin = ndvi_moyen_periode(ndvi[n - k:], pile[n - k:])
                ecart = moyenne_fin - moyenne_debut
                ndvi_connu[r0:r1] = np.isfinite(ecart)
                with np.errstate(invalid="ignore"):
                    baisse_ndvi[r0:r1] = ecart < -SEUIL_BAISSE_NDVI_X10000
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

            for nom in ("occupation", "progression", "productivite"):
                _ecrire_lignes(sorties[nom], cartes[nom][r0:r1], r0)

        # 4. Synthèse : les taches de 0,5 ha se calculent sur toute la zone
        signaler("Synthèse : perte confirmée par le NDVI, taches d'au moins 0,5 ha", 86)
        if avec_ndvi:
            cartes["synthese"] = synthese_regle(cartes["occupation"], baisse_ndvi, ndvi_connu)
        del baisse_ndvi, ndvi_connu
        for r0 in range(0, H, LIGNES_PAR_BLOC):
            _ecrire_lignes(sorties["synthese"], cartes["synthese"][r0:r0 + LIGNES_PAR_BLOC], r0)

        emprise = _emprise_wgs84(ref)
        fenetre = _fenetre_validite(ref)
    finally:
        for s in sources + sources_ndvi + list(sorties.values()):
            s.close()
        if sortie_pente is not None:
            sortie_pente.close()

    signaler("Statistiques et images", 92)
    if comptes_commun[0].sum() == 0:
        raise ErreurSerie("Aucun pixel n'est classé à toutes les dates.")

    # --- Séries de surfaces (toute la zone et terre ferme), avec terres nues ---
    noms_series = list(CLASSES) + ["Terres nues (sol nu + sable/dune)",
                                   "Végétation (dense + clairsemée)"]

    def serie(comptes):
        total_ha = int(comptes[0].sum()) * HECTARES_PAR_PIXEL
        lignes = []
        for i, d in enumerate(dates):
            ha = [round(int(c) * HECTARES_PAR_PIXEL, 2) for c in comptes[i]]
            ha.append(round(ha[3] + ha[4], 2))
            ha.append(round(ha[1] + ha[2], 2))
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
        valeurs = compter_codes(carte)
        total = int(sum(valeurs[code] for code, _, _ in categories))
        bilans[nom] = {
            "titre": titre,
            "disponible": total > 0,
            "hectares_compares": round(total * HECTARES_PAR_PIXEL, 2),
            "hectares_indetermines": round(int(valeurs[INDETERMINE]) * HECTARES_PAR_PIXEL, 2),
            "categories": [{
                "code": code, "nom": libelle, "couleur": couleur,
                "hectares": round(int(valeurs[code]) * HECTARES_PAR_PIXEL, 2),
                "pourcentage": round(100.0 * int(valeurs[code]) / total, 2) if total else 0.0,
            } for code, libelle, couleur in categories],
        }
        if total:
            ecrire_png(carte, categories, dossier / f"{nom}.png")

    # Détail des surfaces stables et pertes candidates (règle du mémoire)
    detail = {
        "eau_stable_ha": round(int(stables[ETAT_EAU]) * HECTARES_PAR_PIXEL, 2),
        "vegetation_stable_ha": round(int(stables[ETAT_VEGETATION]) * HECTARES_PAR_PIXEL, 2),
        "surface_nue_persistante_ha": round(int(stables[ETAT_NU]) * HECTARES_PAR_PIXEL, 2),
        "pertes_candidates_ha": round(int((cartes["occupation"] == DEGRADATION).sum())
                                      * HECTARES_PAR_PIXEL, 2),
        "gains_ha": round(int((cartes["occupation"] == AMELIORATION).sum()) * HECTARES_PAR_PIXEL, 2),
    }

    # Domaine de validité : partie de la zone dans la zone d'entraînement
    validite = {"emprise_entrainement": list(EMPRISE_ENTRAINEMENT), "part_zone": 0.0,
                "hectares_zone": round(H * L * HECTARES_PAR_PIXEL, 2), "hectares_dedans": 0.0,
                "synthese_dedans": None}
    if fenetre is not None:
        f0, f1, g0, g1 = fenetre
        dedans = (f1 - f0) * (g1 - g0)
        validite["part_zone"] = round(100.0 * dedans / (H * L), 1)
        validite["hectares_dedans"] = round(dedans * HECTARES_PAR_PIXEL, 2)
        if bilans["synthese"]["disponible"]:
            v = compter_codes(cartes["synthese"][f0:f1, g0:g1])
            validite["synthese_dedans"] = [{
                "code": code, "nom": libelle,
                "hectares": round(int(v[code]) * HECTARES_PAR_PIXEL, 2)}
                for code, libelle, _ in CARTES["synthese"][1]]
    validite["statut"] = ("valide" if validite["part_zone"] >= 99.0 else
                          "partiel" if validite["part_zone"] > 0 else "indicatif")

    # Persistance : part des pertes candidates qui sont aussi progressives
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

    # --- Contrôle de qualité des images ---
    qualite = controle_qualite(dates, [100.0 * int(v) / (H * L) for v in non_classes],
                               ndvi_dates["medianes"] if ndvi_dates else None)

    # --- Avertissements ---
    mois = sorted({d[5:7] for d in dates})
    avertissements = list(qualite["avertissements"])
    if len(mois) > 1:
        avertissements.append(
            "Les images ne sont pas toutes du même mois (" + ", ".join(mois) + ") : une partie "
            "des différences peut venir de la saison et non d'une évolution.")
    if n < 10:
        avertissements.append(f"Série de {n} dates : les tests de tendance ont peu de puissance.")
    if not par_pixel:
        avertissements.append(f"Moins de {MIN_DATES_TENDANCE} dates : pas de tendance par pixel.")
    if not avec_ndvi:
        avertissements.append("NDVI indisponible pour au moins une date : le contrôle des pertes, "
                              "la productivité et la synthèse n'ont pas pu être calculés.")
    if k == 1:
        avertissements.append(f"Moins de {MIN_DATES_PERIODES} dates : la première date est comparée "
                              "à la dernière, sans vote sur 3 dates (résultat plus sensible au bruit).")
    if validite["statut"] == "indicatif":
        avertissements.append("La zone est entièrement hors de la zone d'entraînement du modèle : "
                              "les résultats sont indicatifs (non validés sur cette zone).")
    elif validite["statut"] == "partiel":
        avertissements.append(f"Seuls {validite['part_zone']} % de la zone sont dans la zone "
                              "d'entraînement du modèle : le reste est indicatif.")

    resume = {
        "version": VERSION,
        "regle": REGLE,
        "detail": detail,
        "validite": validite,
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
        "qualite": qualite,
        "avertissements": avertissements,
    }
    with open(dossier / "resume.json", "w", encoding="utf-8") as f:
        json.dump(resume, f, ensure_ascii=False, indent=2)
    return resume
