"""
DeepDesert - modules M5 (comparaison) et M6 (confirmation)
==========================================================

Détecte les pertes durables de végétation à partir des analyses déjà faites par
l'application (un fichier classes.tif par archive Sentinel-2), avec EXACTEMENT les
règles du notebook v7 / v8 :

  * période de référence = 3 premières années, période récente = 3 dernières ;
  * état d'un pixel = eau / végétation / surface nue observé au moins 2 années sur 3 ;
  * perte candidate = végétation -> surface nue ;
  * perte confirmée = perte candidate + baisse du NDVI moyen de plus de 0,10,
    en taches d'au moins 0,5 ha (50 pixels de 10 m) ;
  * tendances des surfaces : test de Mann-Kendall et pente de Sen.

Le NDVI est recalculé à partir des bandes B04 et B08 des archives .zip (même formule
qu'à l'entraînement, décalage radiométrique -1000 appliqué quand il existe).

Utilisation (dans le dossier detection_desertification, environnement .venv activé) :

    python scripts/detecter_changements.py --analyses donnees --imports donnees/imports --mois 2

Sorties (dossier donnees/changements/mois_02 par défaut) :
    synthese.tif        carte de synthèse, codes 0 à 8 (à ouvrir dans QGIS)
    synthese.png        aperçu avec légende
    surfaces.csv        surface de chaque catégorie (tuile entière et emprise d'étude)
    tendances.csv       Mann-Kendall et pente de Sen par grande classe
    foyers.csv          foyers de pertes confirmées (taches à moins de 250 m regroupées)
    foyers.kml          les mêmes foyers, à ouvrir dans Google Earth
    resume.json         paramètres, années utilisées et chiffres clés
"""
import argparse
import csv
import json
import math
import re
import sys
import time
import zipfile
from pathlib import Path

import numpy as np

try:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.vrt import WarpedVRT
    from rasterio.windows import Window
    from rasterio.warp import transform as transformer_coords
except ImportError:  # pragma: no cover
    sys.exit("rasterio est introuvable : activez l'environnement .venv du projet.")
try:
    from scipy import ndimage
except ImportError:  # pragma: no cover
    sys.exit("scipy est introuvable : installez-le avec  pip install scipy")

# ---------------------------------------------------------------- paramètres du v7
N_PERIODE, N_MIN = 3, 2
SEUIL_NDVI = 0.10
TAILLE_MIN_PX = 50            # 0,5 ha
RAYON_FOYER_M = 250
HA_PAR_PIXEL = 0.01           # pixel de 10 m
fmt = lambda v: f'{v:,}'.replace(',', ' ')
NODATA_CLASSE = 255
# emprise d'étude du mémoire (grille du notebook : origine 434960 E / 1525690 N, 7168 x 7808 pixels)
EMPRISE_ETUDE = (434960.0, 1525690.0 - 7808 * 10.0, 434960.0 + 7168 * 10.0, 1525690.0)  # xmin, ymin, xmax, ymax

CAT = ['Indéterminé', 'Eau stable', 'Végétation stable', 'Surface nue persistante', "Changement lié à l'eau",
       'Gain de végétation (≥ 0,5 ha)', 'Petit gain (< 0,5 ha)', 'Perte non confirmée par le NDVI',
       'Perte confirmée (U-Net + NDVI, ≥ 0,5 ha)']
COUL = ['#d9d9d9', '#2166ac', '#c7e9c0', '#f3e3b5', '#92c5de', '#006d2c', '#74c476', '#fdae61', '#b2182b']


# ---------------------------------------------------------------- règles (identiques au v8)
def etat_dominant(pile, n_min=N_MIN):
    eau = (pile == 0).sum(0)
    veg = np.isin(pile, [1, 2]).sum(0)
    nu = np.isin(pile, [3, 4]).sum(0)
    etat = np.full(pile.shape[1:], 255, dtype=np.uint8)
    etat[eau >= n_min] = 0
    etat[veg >= n_min] = 1
    etat[nu >= n_min] = 2
    ambigu = ((eau >= n_min).astype(int) + (veg >= n_min).astype(int) + (nu >= n_min).astype(int)) > 1
    etat[ambigu] = 255
    return etat


def carte_changement(pile_debut, pile_fin, n_min=N_MIN):
    d, f = etat_dominant(pile_debut, n_min), etat_dominant(pile_fin, n_min)
    ch = np.zeros(d.shape, dtype=np.uint8)
    ok = (d != 255) & (f != 255)
    ch[ok & (d == 0) & (f == 0)] = 1      # eau stable
    ch[ok & (d == 1) & (f == 1)] = 2      # végétation stable
    ch[ok & (d == 2) & (f == 2)] = 3      # surface nue persistante
    ch[ok & (d == 1) & (f == 2)] = 4      # perte candidate
    ch[ok & (d == 2) & (f == 1)] = 5      # gain
    ch[ok & ((d == 0) != (f == 0))] = 6   # changement lié à l'eau
    return ch


def filtrer_petites_taches(masque_bool, taille_min=TAILLE_MIN_PX):
    etiquettes, n = ndimage.label(masque_bool)
    if n == 0:
        return masque_bool
    garder = np.bincount(etiquettes.ravel()) >= taille_min
    garder[0] = False
    return garder[etiquettes]


def moyenne_valide(pile):
    """pile float32 avec NaN pour les valeurs absentes -> moyenne des valeurs présentes."""
    n = np.isfinite(pile).sum(0)
    s = np.nansum(pile, axis=0)
    with np.errstate(invalid='ignore', divide='ignore'):
        return np.where(n > 0, s / np.maximum(n, 1), np.nan).astype(np.float32)


def synthese(ch, emprise, d_ndvi):
    perte = (ch == 4) & emprise & np.isfinite(d_ndvi)
    confirmee = filtrer_petites_taches(perte & (d_ndvi < -SEUIL_NDVI))
    gain = (ch == 5) & emprise
    gain_grand = filtrer_petites_taches(gain)
    syn = np.zeros(ch.shape, np.uint8)
    syn[ch == 1] = 1
    syn[ch == 2] = 2
    syn[ch == 3] = 3
    syn[ch == 6] = 4
    syn[gain] = 6
    syn[gain_grand] = 5
    syn[ch == 4] = 7
    syn[confirmee] = 8
    syn[~emprise] = 0
    return syn


# ---------------------------------------------------------------- statistiques
def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def mann_kendall(annees, valeurs):
    """Test de Mann-Kendall (approximation normale, comme pymannkendall.original_test) et pente de Sen."""
    x = np.asarray(annees, float)
    y = np.asarray(valeurs, float)
    n = len(y)
    s = 0.0
    for i in range(n - 1):
        s += np.sign(y[i + 1:] - y[i]).sum()
    _, nb = np.unique(y, return_counts=True)
    var = (n * (n - 1) * (2 * n + 5) - np.sum(nb * (nb - 1) * (2 * nb + 5))) / 18.0
    if s > 0:
        z = (s - 1) / math.sqrt(var)
    elif s < 0:
        z = (s + 1) / math.sqrt(var)
    else:
        z = 0.0
    p = 2 * (1 - _phi(abs(z)))
    pentes = [(y[j] - y[i]) / (x[j] - x[i]) for i in range(n) for j in range(i + 1, n) if x[j] != x[i]]
    return s, p, float(np.median(pentes))


def foyers_par_blocs(masque, pixel_m, rayon_m=RAYON_FOYER_M, bloc=2048):
    """Regroupe les taches séparées de moins de rayon_m (même définition que le v8),
    calculé par bandes de lignes pour tenir dans la mémoire d'un ordinateur portable."""
    marge = int(math.ceil(rayon_m / 2 / pixel_m)) + 1
    proche = np.zeros(masque.shape, bool)
    for l0 in range(0, masque.shape[0], bloc):
        l1 = min(l0 + bloc, masque.shape[0])
        a0, a1 = max(0, l0 - marge), min(masque.shape[0], l1 + marge)
        sous = masque[a0:a1]
        if not sous.any():
            continue
        cols = np.where(sous.any(0))[0]
        c0, c1 = max(0, cols[0] - marge), min(masque.shape[1], cols[-1] + 1 + marge)
        d = ndimage.distance_transform_edt(~sous[:, c0:c1], sampling=pixel_m)
        proche[l0:l1, c0:c1] = (d <= rayon_m / 2)[l0 - a0:l0 - a0 + (l1 - l0)]
    groupes, n = ndimage.label(proche, structure=np.ones((3, 3)))
    del proche
    groupes = np.where(masque, groupes, 0)
    surf = np.bincount(groupes.ravel(), minlength=n + 1) * HA_PAR_PIXEL
    surf[0] = 0
    return groupes, surf


# ---------------------------------------------------------------- recherche des fichiers
RE_PRODUIT = re.compile(r'(S2[ABC]_MSIL2A_(\d{8})T\d{6}_N\d{4}_R\d{3}_T\w{5}_\d{8}T\d{6})')
RE_DATE = re.compile(r'(20\d{2})-?(\d{2})-?(\d{2})')


def infos_analyse(chemin_tif):
    """Date et nom du produit Sentinel-2 d'une analyse, lus dans les fichiers voisins (resume.json...)."""
    textes = []
    for f in sorted(chemin_tif.parent.glob('*.json')):
        try:
            textes.append(f.read_text(encoding='utf-8', errors='ignore'))
        except OSError:
            pass
    textes.append(str(chemin_tif.parent))
    tout = '\n'.join(textes)
    m = RE_PRODUIT.search(tout)
    if m:
        d = m.group(2)
        return f'{d[:4]}-{d[4:6]}-{d[6:]}', m.group(1)
    for m in RE_DATE.finditer(tout):
        a, mo, j = m.groups()
        if 1 <= int(mo) <= 12 and 1 <= int(j) <= 31:
            return f'{a}-{mo}-{j}', None
    return None, None


def trouver_archive(dossier_imports, date, produit):
    zips = list(Path(dossier_imports).rglob('*.zip'))
    if produit:
        for z in zips:
            if z.name.startswith(produit):
                return z
    cle = date.replace('-', '')
    candidats = [z for z in zips if 'MSIL2A' in z.name and f'_{cle}T' in z.name]
    return candidats[0] if candidats else None


def bandes_et_decalage(chemin_zip):
    """Chemins GDAL des bandes B04 et B08 à 10 m dans l'archive, et décalage BOA_ADD_OFFSET (0 ou -1000)."""
    with zipfile.ZipFile(chemin_zip) as z:
        noms = z.namelist()
        b4 = [n for n in noms if n.endswith('_B04_10m.jp2')]
        b8 = [n for n in noms if n.endswith('_B08_10m.jp2')]
        mtd = [n for n in noms if n.endswith('MTD_MSIL2A.xml')]
        decalage = 0
        if mtd:
            xml = z.read(mtd[0]).decode('utf-8', errors='ignore')
            m = re.search(r'BOA_ADD_OFFSET[^>]*>\s*(-?\d+)', xml)
            if m:
                decalage = int(m.group(1))
    if not b4 or not b8:
        raise ValueError(f"bandes B04/B08 à 10 m introuvables dans {chemin_zip.name}")
    base = '/vsizip/' + chemin_zip.resolve().as_posix()
    return f'{base}/{b4[0]}', f'{base}/{b8[0]}', decalage


def ouvrir_sur_grille(chemin, ref, resampling=Resampling.nearest, nodata=None):
    src = rasterio.open(chemin)
    if src.crs == ref['crs'] and src.transform == ref['transform'] and (src.width, src.height) == (ref['width'], ref['height']):
        return src, src
    vrt = WarpedVRT(src, crs=ref['crs'], transform=ref['transform'], width=ref['width'], height=ref['height'],
                    resampling=resampling, nodata=nodata if nodata is not None else src.nodata)
    return src, vrt


# ---------------------------------------------------------------- programme principal
def main():
    ap = argparse.ArgumentParser(description="DeepDesert M5-M6 : détection des pertes durables de végétation.")
    ap.add_argument('--analyses', default='donnees', help="dossier où chercher les classes.tif des analyses")
    ap.add_argument('--imports', default='donnees/imports', help="dossier des archives Sentinel-2 (.zip)")
    ap.add_argument('--mois', default='2', help="mois retenus, par exemple 2 ou 1,2")
    ap.add_argument('--sortie', default=None, help="dossier des résultats")
    ap.add_argument('--fichier', default='classes.tif', help="nom du fichier de classes écrit par chaque analyse")
    ap.add_argument('--bloc', type=int, default=1024, help="hauteur des bandes de calcul (pixels)")
    args = ap.parse_args()
    mois = {int(m) for m in args.mois.split(',')}
    sortie = Path(args.sortie or f"donnees/changements/mois_{'_'.join(f'{m:02d}' for m in sorted(mois))}")
    sortie.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    # 1. Inventaire des analyses -------------------------------------------------
    trouvees = []
    for tif in Path(args.analyses).rglob(args.fichier):
        if sortie in tif.parents:
            continue
        date, produit = infos_analyse(tif)
        if date is None or int(date[5:7]) not in mois:
            continue
        with rasterio.open(tif) as s:
            taille = s.width * s.height
        trouvees.append({'tif': tif, 'date': date, 'produit': produit, 'taille': taille})
    par_annee = {}
    for a in sorted(trouvees, key=lambda r: (r['date'][:4], -r['taille'])):
        par_annee.setdefault(int(a['date'][:4]), a)   # une analyse par année : la plus grande zone
    annees = sorted(par_annee)
    print("Analyses retenues (une par année, la plus grande zone analysée) :")
    for a in annees:
        r = par_annee[a]
        print(f"  {a}  {r['date']}  {r['taille'] / 1e6:6.1f} Mpx  {r['tif']}")
    if len(annees) < 2 * N_PERIODE:
        sys.exit(f"Il faut au moins {2 * N_PERIODE} années différentes ; trouvées : {len(annees)}. "
                 "Vérifiez --analyses et --mois (par exemple --mois 1,2 si certaines archives sont de janvier).")
    deb, fin = annees[:N_PERIODE], annees[-N_PERIODE:]
    print(f"Période de référence : {deb} ; période récente : {fin}")

    # 2. Grille de référence = la plus grande analyse ----------------------------
    ref_tif = max((par_annee[a] for a in annees), key=lambda r: r['taille'])['tif']
    with rasterio.open(ref_tif) as s:
        REF = {'crs': s.crs, 'transform': s.transform, 'width': s.width, 'height': s.height}
    H, W, PIXEL = REF['height'], REF['width'], abs(REF['transform'].a)
    print(f"Grille : {W} x {H} pixels de {PIXEL:.0f} m ({fmt(round(W * H * PIXEL * PIXEL / 1e6))} km²), {REF['crs']}")

    # 3. Archives pour le NDVI ----------------------------------------------------
    bandes = {}
    for a in deb + fin:
        r = par_annee[a]
        z = trouver_archive(args.imports, r['date'], r['produit'])
        if z is None:
            sys.exit(f"Archive Sentinel-2 du {r['date']} introuvable dans {args.imports} : elle sert à calculer le NDVI.")
        bandes[a] = bandes_et_decalage(z)
        print(f"  NDVI {a} : {z.name} (décalage {bandes[a][2]})")

    # 4. Ouverture de tous les rasters sur la même grille -------------------------
    ouverts, lecteurs_cl, lecteurs_b = [], {}, {}
    for a in annees:
        s, v = ouvrir_sur_grille(par_annee[a]['tif'], REF, nodata=NODATA_CLASSE)
        ouverts.append(s); lecteurs_cl[a] = v
    for a in deb + fin:
        b4, b8, dec = bandes[a]
        s4, v4 = ouvrir_sur_grille(b4, REF, nodata=0)
        s8, v8 = ouvrir_sur_grille(b8, REF, nodata=0)
        ouverts += [s4, s8]; lecteurs_b[a] = (v4, v8, dec)

    ch = np.zeros((H, W), np.uint8)
    emprise = np.zeros((H, W), bool)
    d_ndvi = np.full((H, W), np.nan, np.float32)
    comptes = {a: np.zeros(3, np.int64) for a in annees}   # eau, végétation, surface nue (pixels valides partout)
    n_valides_partout = 0

    def ndvi(a, fen, classe):
        v4, v8, dec = lecteurs_b[a]
        r = v4.read(1, window=fen).astype(np.float32)
        n = v8.read(1, window=fen).astype(np.float32)
        ok = (r > 0) & (n > 0) & (classe != NODATA_CLASSE)
        r = (r + dec) / 10000.0
        n = (n + dec) / 10000.0
        with np.errstate(invalid='ignore', divide='ignore'):
            v = (n - r) / (n + r)
        v[~ok | ~np.isfinite(v)] = np.nan
        return v

    print("Calcul par bandes de lignes...")
    for l0 in range(0, H, args.bloc):
        h = min(args.bloc, H - l0)
        fen = Window(0, l0, W, h)
        pile = {a: lecteurs_cl[a].read(1, window=fen) for a in annees}
        tous = np.stack([pile[a] for a in annees])
        valide_tous = (tous != NODATA_CLASSE).all(0)
        n_valides_partout += int(valide_tous.sum())
        for a in annees:
            c = pile[a][valide_tous]
            comptes[a] += [(c == 0).sum(), np.isin(c, [1, 2]).sum(), np.isin(c, [3, 4]).sum()]
        p_deb = np.stack([pile[a] for a in deb]); p_fin = np.stack([pile[a] for a in fin])
        ch[l0:l0 + h] = carte_changement(p_deb, p_fin)
        emprise[l0:l0 + h] = (p_deb != NODATA_CLASSE).any(0) | (p_fin != NODATA_CLASSE).any(0)
        if (ch[l0:l0 + h] == 4).any():
            n_deb = moyenne_valide(np.stack([ndvi(a, fen, pile[a]) for a in deb]))
            n_fin = moyenne_valide(np.stack([ndvi(a, fen, pile[a]) for a in fin]))
            d_ndvi[l0:l0 + h] = n_fin - n_deb
        print(f"  lignes {l0 + h:>6} / {H}  ({time.time() - t0:5.0f} s)", end='\r')
    print()
    for s in ouverts:
        s.close()

    # 5. Synthèse ----------------------------------------------------------------
    syn = synthese(ch, emprise, d_ndvi)
    del ch
    profil = {'driver': 'GTiff', 'height': H, 'width': W, 'count': 1, 'dtype': 'uint8', 'crs': REF['crs'],
              'transform': REF['transform'], 'nodata': 255, 'compress': 'deflate', 'tiled': True}
    with rasterio.open(sortie / 'synthese.tif', 'w', **profil) as dst:
        dst.write(syn, 1)

    # masque de l'emprise d'étude du mémoire, pour comparer avec le notebook
    tr = REF['transform']
    c0 = int(round((EMPRISE_ETUDE[0] - tr.c) / tr.a)); c1 = int(round((EMPRISE_ETUDE[2] - tr.c) / tr.a))
    r0 = int(round((EMPRISE_ETUDE[3] - tr.f) / tr.e)); r1 = int(round((EMPRISE_ETUDE[1] - tr.f) / tr.e))
    r0c, r1c, c0c, c1c = max(r0, 0), min(r1, H), max(c0, 0), min(c1, W)
    etude = np.zeros((H, W), bool)
    if r1c > r0c and c1c > c0c:
        etude[r0c:r1c, c0c:c1c] = True
    part_etude = 100 * etude.sum() / (7168 * 7808)

    lignes, n_emp, n_emp_etude = [], int(emprise.sum()), int((emprise & etude).sum())
    for k in range(9):
        n = int((syn[emprise] == k).sum()); ne = int((syn[emprise & etude] == k).sum())
        lignes.append({'Code': k, 'Catégorie': CAT[k],
                       'Tuile (ha)': round(n * HA_PAR_PIXEL), 'Tuile (%)': round(100 * n / max(n_emp, 1), 2),
                       "Emprise d'étude (ha)": round(ne * HA_PAR_PIXEL), "Emprise d'étude (%)": round(100 * ne / max(n_emp_etude, 1), 2)})
    with open(sortie / 'surfaces.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(lignes[0]), delimiter=';'); w.writeheader(); w.writerows(lignes)

    # 6. Tendances ---------------------------------------------------------------
    tend = []
    for i, nom in enumerate(['Eau', 'Végétation', 'Surface nue']):
        parts = [100 * comptes[a][i] / max(n_valides_partout, 1) for a in annees]
        s, p, pente = mann_kendall(annees, parts)
        tend.append({'Classe': nom, **{str(a): round(v, 2) for a, v in zip(annees, parts)},
                     'S': int(s), 'p': round(p, 3), 'Pente de Sen (pt/an)': round(pente, 3),
                     'Tendance (p < 0,05)': ('hausse' if pente > 0 else 'baisse') if p < 0.05 else 'aucune'})
    with open(sortie / 'tendances.csv', 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(tend[0]), delimiter=';'); w.writeheader(); w.writerows(tend)

    # 7. Foyers de pertes confirmées ---------------------------------------------
    confirmee = syn == 8
    groupes, surf = foyers_par_blocs(confirmee, PIXEL)
    ordre = [g for g in np.argsort(surf)[::-1] if surf[g] > 0]
    objets = ndimage.find_objects(groupes)
    foyers = []
    for rang, g in enumerate(ordre[:50], 1):
        sl = objets[g - 1]
        sous = groupes[sl] == g
        lc, cc = ndimage.center_of_mass(sous)
        lc += sl[0].start; cc += sl[1].start
        xs = [tr.c + cc * tr.a, tr.c + sl[1].start * tr.a, tr.c + sl[1].stop * tr.a]
        ys = [tr.f + lc * tr.e, tr.f + sl[0].stop * tr.e, tr.f + sl[0].start * tr.e]
        lon, lat = transformer_coords(REF['crs'], 'EPSG:4326', xs, ys)
        dn = d_ndvi[sl][sous]
        foyers.append({'Rang': rang, 'Surface (ha)': round(float(surf[g]), 1),
                       'Latitude centre': round(lat[0], 5), 'Longitude centre': round(lon[0], 5),
                       'Lat min': round(lat[1], 5), 'Lat max': round(lat[2], 5),
                       'Lon min': round(lon[1], 5), 'Lon max': round(lon[2], 5),
                       'Baisse médiane du NDVI': round(float(np.nanmedian(dn)), 3) if dn.size else None,
                       "Dans l'emprise d'étude": bool(etude[int(lc), int(cc)])})
    if foyers:
        with open(sortie / 'foyers.csv', 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.DictWriter(f, fieldnames=list(foyers[0]), delimiter=';'); w.writeheader(); w.writerows(foyers)
        kml = ['<?xml version="1.0" encoding="UTF-8"?>', '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
               '<name>DeepDesert - foyers de pertes confirmées</name>']
        for fo in foyers:
            box = (f"{fo['Lon min']},{fo['Lat min']},0 {fo['Lon max']},{fo['Lat min']},0 {fo['Lon max']},{fo['Lat max']},0 "
                   f"{fo['Lon min']},{fo['Lat max']},0 {fo['Lon min']},{fo['Lat min']},0")
            kml.append(f"<Placemark><name>Foyer {fo['Rang']} ({fo['Surface (ha)']} ha)</name>"
                       f"<description>Baisse médiane du NDVI : {fo['Baisse médiane du NDVI']}</description>"
                       f"<Style><LineStyle><color>ff2b18b2</color><width>2</width></LineStyle><PolyStyle><fill>0</fill></PolyStyle></Style>"
                       f"<MultiGeometry><Point><coordinates>{fo['Longitude centre']},{fo['Latitude centre']},0</coordinates></Point>"
                       f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{box}</coordinates></LinearRing></outerBoundaryIs></Polygon>"
                       f"</MultiGeometry></Placemark>")
        kml.append('</Document></kml>')
        (sortie / 'foyers.kml').write_text('\n'.join(kml), encoding='utf-8')
    del groupes

    # 8. Aperçu ------------------------------------------------------------------
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
        from matplotlib.colors import ListedColormap, BoundaryNorm
        f = max(1, max(H, W) // 1500)
        h2, w2 = (H // f) * f, (W // f) * f
        bloc_c = syn[:h2, :w2].reshape(h2 // f, f, w2 // f, f)
        meilleur = np.zeros((h2 // f, w2 // f), np.uint8); nmax = np.zeros_like(meilleur, dtype=np.int32)
        for k in range(9):
            n = (bloc_c == k).sum((1, 3))
            if k == 8:
                n = n * 25   # une perte confirmée reste visible même si elle n'occupe qu'une partie du bloc
            plus = n > nmax; meilleur[plus] = k; nmax[plus] = n[plus]
        ext = [tr.c / 1000, (tr.c + w2 * tr.a) / 1000, (tr.f + h2 * tr.e) / 1000, tr.f / 1000]
        fig, ax = plt.subplots(figsize=(11, 11.5))
        ax.imshow(meilleur, cmap=ListedColormap(COUL), norm=BoundaryNorm(np.arange(-0.5, 9.5), 9),
                  extent=ext, interpolation='nearest')
        ax.add_patch(mpatches.Rectangle((EMPRISE_ETUDE[0] / 1000, EMPRISE_ETUDE[1] / 1000),
                                        (EMPRISE_ETUDE[2] - EMPRISE_ETUDE[0]) / 1000, (EMPRISE_ETUDE[3] - EMPRISE_ETUDE[1]) / 1000,
                                        fill=False, ec='black', lw=1.5, ls='--'))
        ax.set_xlabel('UTM, est (km)'); ax.set_ylabel('UTM, nord (km)')
        ax.set_title(f"Synthèse des changements {deb[0]}–{deb[-1]} / {fin[0]}–{fin[-1]} (mois {args.mois})\n"
                     "règle 2 années sur 3, perte confirmée si baisse du NDVI > 0,10 et tache ≥ 0,5 ha ; "
                     "tirets = emprise d'étude", fontsize=10)
        fig.legend(handles=[mpatches.Patch(color=COUL[k], label=CAT[k]) for k in range(9)],
                   loc='lower center', ncol=3, frameon=False, fontsize=9)
        fig.subplots_adjust(bottom=0.12)
        fig.savefig(sortie / 'synthese.png', dpi=130); plt.close(fig)
    except ImportError:
        print("matplotlib absent : aperçu PNG non produit (le GeoTIFF suffit pour QGIS).")

    # 9. Résumé ------------------------------------------------------------------
    ha = lambda k, m=emprise: round(float((syn[m] == k).sum()) * HA_PAR_PIXEL)
    resume = {
        'regles': {'periode': N_PERIODE, 'annees_min': N_MIN, 'seuil_ndvi': SEUIL_NDVI,
                   'taille_min_ha': TAILLE_MIN_PX * HA_PAR_PIXEL, 'rayon_foyer_m': RAYON_FOYER_M},
        'annees': annees, 'reference': deb, 'recente': fin,
        'analyses': {str(a): str(par_annee[a]['tif']) for a in annees},
        'tuile': {'pertes_confirmees_ha': ha(8), 'pertes_candidates_ha': ha(7) + ha(8), 'gains_ha': ha(5),
                  'nombre_foyers': len(ordre)},
        'emprise_etude': {'couverte_pct': round(part_etude, 1), 'pertes_confirmees_ha': ha(8, emprise & etude),
                          'pertes_candidates_ha': ha(7, emprise & etude) + ha(8, emprise & etude),
                          'reference_notebook_v7_fevrier_ha': 159},
        'tendances': tend, 'duree_s': round(time.time() - t0),
    }
    (sortie / 'resume.json').write_text(json.dumps(resume, ensure_ascii=False, indent=2), encoding='utf-8')

    print("\n=== Résultat ===")
    print(f"Tuile entière   : pertes candidates {fmt(resume['tuile']['pertes_candidates_ha'])} ha ; "
          f"pertes confirmées {fmt(resume['tuile']['pertes_confirmees_ha'])} ha ; gains {fmt(resume['tuile']['gains_ha'])} ha ; "
          f"{len(ordre)} foyers")
    print(f"Emprise d'étude ({part_etude:.0f} % couverte) : pertes confirmées {fmt(resume['emprise_etude']['pertes_confirmees_ha'])} ha "
          f"(notebook v7, février : 159 ha)")
    for t in tend:
        print(f"  {t['Classe']:12s} pente {t['Pente de Sen (pt/an)']:+.2f} pt/an, p = {t['p']:.3f} -> {t['Tendance (p < 0,05)']}")
    print(f"Fichiers écrits dans {sortie.resolve()} ({time.time() - t0:.0f} s).")
    print("Rappel : hors de l'emprise d'étude, le modèle n'a pas été évalué ; les résultats y sont indicatifs.")


if __name__ == '__main__':
    main()
