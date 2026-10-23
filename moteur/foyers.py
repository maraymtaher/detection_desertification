"""
Zones à surveiller : foyers de perte de végétation d'un suivi temporel
(carte de synthèse de la méthode version 3, règle du mémoire).

Méthode :
  1. la zone est découpée en carrés de 1 km (100 x 100 pixels) ;
  2. les carrés sont classés par surface de perte confirmée (U-Net +
     baisse du NDVI > 0,10, taches >= 0,5 ha), puis par surface de perte
     non confirmée par le NDVI ;
  3. on retient les premiers, en écartant un carré voisin d'un carré déjà
     retenu, pour que les foyers soient distincts ;
  4. pour chaque foyer, on décrit la trajectoire des pixels dégradés date
     par date (classes du U-Net, NDVI) et on extrait des archives les images
     en vraies couleurs de la première et de la dernière date.

Les foyers sont des « zones à vérifier », pas des zones déclarées désertifiées.
"""
import json
from pathlib import Path

import numpy as np

from moteur.config_modele import CLASSES, EMPRISE_ENTRAINEMENT, NON_CLASSE
from moteur.resultats import HECTARES_PAR_PIXEL
from moteur import serie as S

TAILLE_CARRE = 100          # pixels de 10 m, soit 1 km
MARGE_IMAGE = 50            # pixels ajoutés autour du carré pour les images
NB_FOYERS = 12
GAIN_RVB = 2.5              # même gain que le script « True color » de Sentinel Hub


# ---------------------------------------------------------------------
# Accès aux données (isolés pour pouvoir les tester)
# ---------------------------------------------------------------------
def _lire_fenetre(src, r0, r1, c0, c1):
    return src.read(1, window=((r0, r1), (c0, c1)))


def _vers_wgs84(src, lignes, colonnes):
    """Coordonnées (lon, lat) des points (ligne, colonne) de la grille."""
    from rasterio.warp import transform
    xs, ys = [], []
    for l, c in zip(lignes, colonnes):
        x, y = src.transform * (c, l)
        xs.append(x)
        ys.append(y)
    lon, lat = transform(src.crs, "EPSG:4326", xs, ys)
    return list(zip(lon, lat))


def lire_rvb(chemin_zip, fenetre):
    """Image en vraies couleurs (uint8) d'une fenêtre (l0, c0, l1, c1) de la tuile,
    avec le même étirement fixe pour toutes les dates (comparaison possible)."""
    import rasterio
    from rasterio.windows import Window
    from moteur.lecture_safe import lire_produit
    produit = lire_produit(chemin_zip)
    l0, c0, l1, c1 = fenetre
    canaux = []
    for nom in ("B4", "B3", "B2"):
        with rasterio.open(produit.chemin_gdal(produit.bandes[nom])) as src:
            v = src.read(1, window=Window(c0, l0, c1 - c0, l1 - l0), boundless=True, fill_value=0)
        r = np.maximum(v.astype(np.float32) + produit.decalages.get(nom, 0.0), 0) / produit.quantification
        canaux.append(r)
    rvb = np.clip(np.stack(canaux, axis=-1) * GAIN_RVB, 0, 1)
    return (rvb * 255).astype(np.uint8)


# ---------------------------------------------------------------------
# Calculs
# ---------------------------------------------------------------------
def sommes_carres(masque, taille=TAILLE_CARRE):
    """Nombre de pixels vrais dans chaque carré de taille x taille."""
    H, L = masque.shape
    nh, nl = -(-H // taille), -(-L // taille)
    resultat = np.zeros((nh, nl), dtype=np.int64)
    for i in range(nh):                       # une bande de carrés à la fois (mémoire limitée)
        bande = np.zeros((taille, nl * taille), dtype=np.int32)
        morceau = masque[i * taille:(i + 1) * taille]
        bande[:morceau.shape[0], :L] = morceau
        resultat[i] = bande.reshape(taille, nl, taille).sum(axis=(0, 2))
    return resultat


def choisir_carres(confirme, un_seul, nombre=NB_FOYERS):
    """Indices (i, j) des carrés retenus, du plus au moins dégradé."""
    ordre = np.lexsort((-un_seul.ravel(), -confirme.ravel()))
    retenus = []
    for k in ordre:
        i, j = divmod(int(k), confirme.shape[1])
        if confirme[i, j] == 0 and un_seul[i, j] == 0:
            break
        if any(abs(i - a) <= 1 and abs(j - b) <= 1 for a, b in retenus):
            continue
        retenus.append((i, j))
        if len(retenus) == nombre:
            break
    return retenus


def contour(masque):
    """Pixels du bord d'un masque (masque moins son érosion 4-connexe)."""
    m = masque.astype(bool)
    erode = m.copy()
    erode[1:, :] &= m[:-1, :]
    erode[:-1, :] &= m[1:, :]
    erode[:, 1:] &= m[:, :-1]
    erode[:, :-1] &= m[:, 1:]
    return m & ~erode


AGRANDIR = 2


def _agrandir(img):
    return np.repeat(np.repeat(img, AGRANDIR, axis=0), AGRANDIR, axis=1)


def _image_foyer(rvb, chemin):
    from PIL import Image
    Image.fromarray(_agrandir(rvb)).save(chemin)


def _image_contour(masque, chemin):
    """Calque transparent : contour rouge des pixels de perte confirmée,
    superposé dans la page (et masquable) pour ne pas cacher l'image."""
    from PIL import Image
    calque = np.zeros(masque.shape + (4,), dtype=np.uint8)
    calque[contour(masque)] = (255, 40, 40, 255)
    Image.fromarray(_agrandir(calque)).save(chemin)


def dans_validite(centre):
    """Vrai si le point (lon, lat) est dans la zone d'entraînement du modèle."""
    o, s, e, n = EMPRISE_ENTRAINEMENT
    return bool(o <= centre[0] <= e and s <= centre[1] <= n)


def detecter_foyers(dossier_suivi, entrees, nombre=NB_FOYERS, etape=None):
    """
    entrees : liste triée par date de dict {date, classes, ndvi, archive, zone_pixels}.
    Écrit dans dossier_suivi/foyers : foyers.json, foyers.kml, foyers.geojson
    et les images avant/après. Renvoie le résumé.
    """
    signaler = etape or (lambda texte, part: None)
    dossier = Path(dossier_suivi)
    sortie = dossier / "foyers"
    sortie.mkdir(parents=True, exist_ok=True)
    n = len(entrees)
    k = S.TAILLE_PERIODE if n >= S.MIN_DATES_PERIODES else 1

    signaler("Recherche des foyers de perte de végétation", 5)
    src_syn = S._ouvrir(dossier / "synthese.tif")
    src_prog = S._ouvrir(dossier / "progression.tif")
    try:
        synthese = S._lire_lignes(src_syn, 0, src_syn.height)
        conf_c = sommes_carres(synthese == S.CONFIRMEE)
        un_c = sommes_carres(synthese == S.DEGRADATION)
        carres = choisir_carres(conf_c, un_c, nombre)
        H, L = synthese.shape

        foyers = []
        sources = [S._ouvrir(e["classes"]) for e in entrees]
        sources_ndvi = [S._ouvrir(e["ndvi"]) for e in entrees]
        try:
            for numero, (i, j) in enumerate(carres, start=1):
                signaler(f"Description du foyer {numero} sur {len(carres)}",
                         10 + int(80 * (numero - 1) / max(len(carres), 1)))
                r0, c0 = i * TAILLE_CARRE, j * TAILLE_CARRE
                r1, c1 = min(r0 + TAILLE_CARRE, H), min(c0 + TAILLE_CARRE, L)
                syn = synthese[r0:r1, c0:c1]
                degrade = (syn == S.CONFIRMEE) | (syn == S.DEGRADATION)
                prog = _lire_fenetre(src_prog, r0, r1, c0, c1)

                # Trajectoire des pixels dégradés, date par date
                cartes = [_lire_fenetre(s, r0, r1, c0, c1) for s in sources]
                ndvis = [_lire_fenetre(s, r0, r1, c0, c1) for s in sources_ndvi]
                trajectoire = []
                for e, carte, ndvi in zip(entrees, cartes, ndvis):
                    valeurs = carte[degrade]
                    comptes = np.bincount(valeurs[valeurs != NON_CLASSE], minlength=len(CLASSES))
                    total = int(comptes[:len(CLASSES)].sum())
                    nd = ndvi[degrade]
                    nd = nd[nd != S.SANS_DONNEE_NDVI]
                    trajectoire.append({
                        "date": e["date"],
                        "classes": [round(100.0 * int(c) / total, 1) if total else 0.0
                                    for c in comptes[:len(CLASSES)]],
                        "ndvi": round(float(np.median(nd)) / 10000.0, 4) if nd.size else None,
                    })

                # Transition la plus fréquente parmi les pixels confirmés (ou dégradés)
                cible = (syn == S.CONFIRMEE) if (syn == S.CONFIRMEE).any() else degrade
                debut = S.classe_majoritaire(cartes[:k])[cible]
                fin = S.classe_majoritaire(cartes[n - k:])[cible]
                ok = (debut != NON_CLASSE) & (fin != NON_CLASSE)
                paires = np.bincount(debut[ok].astype(np.int64) * len(CLASSES) + fin[ok],
                                     minlength=len(CLASSES) ** 2)
                a, b = divmod(int(paires.argmax()), len(CLASSES))
                transition = {"de": CLASSES[a], "vers": CLASSES[b],
                              "part": round(100.0 * int(paires.max()) / max(int(ok.sum()), 1), 1)}

                nb_degrade = int(degrade.sum())
                coins = _vers_wgs84(src_syn, [r0, r0, r1, r1], [c0, c1, c1, c0])
                centre = _vers_wgs84(src_syn, [(r0 + r1) / 2], [(c0 + c1) / 2])[0]
                foyer = {
                    "numero": numero,
                    "carre": [r0, c0, r1, c1],
                    "centre": [round(centre[0], 5), round(centre[1], 5)],
                    "contour": [[round(x, 6), round(y, 6)] for x, y in coins],
                    "hectares_confirmes": round(int((syn == S.CONFIRMEE).sum()) * HECTARES_PAR_PIXEL, 2),
                    "hectares_un_indicateur": round(int((syn == S.DEGRADATION).sum()) * HECTARES_PAR_PIXEL, 2),
                    "hectares_non_confirmes": round(int((syn == S.DEGRADATION).sum()) * HECTARES_PAR_PIXEL, 2),
                    "dans_validite": dans_validite(centre),
                    "part_progressive": round(100.0 * int((degrade & (prog == S.DEGRADATION)).sum())
                                              / nb_degrade, 1) if nb_degrade else 0.0,
                    "part_carre_valide": round(100.0 * int((syn != S.INDETERMINE).sum()) / syn.size, 1),
                    "transition": transition,
                    "trajectoire": trajectoire,
                    "images": [],
                }

                # Images en vraies couleurs : première et dernière date
                f0, g0 = max(r0 - MARGE_IMAGE, 0), max(c0 - MARGE_IMAGE, 0)
                f1, g1 = min(r1 + MARGE_IMAGE, H), min(c1 + MARGE_IMAGE, L)
                masque = np.zeros((f1 - f0, g1 - g0), dtype=bool)
                masque[r0 - f0:r1 - f0, c0 - g0:c1 - g0] = syn == S.CONFIRMEE
                for rang, e in (("avant", entrees[0]), ("apres", entrees[-1])):
                    lz, cz = e["zone_pixels"][0], e["zone_pixels"][1]
                    try:
                        rvb = lire_rvb(e["archive"], (lz + f0, cz + g0, lz + f1, cz + g1))
                    except Exception:                       # archive absente ou illisible
                        continue
                    nom = f"foyer_{numero:02d}_{rang}.png"
                    _image_foyer(rvb, sortie / nom)
                    foyer["images"].append({"date": e["date"], "fichier": nom, "rang": rang})
                if foyer["images"]:
                    _image_contour(masque, sortie / f"foyer_{numero:02d}_contour.png")
                foyers.append(foyer)
        finally:
            for s in sources + sources_ndvi:
                s.close()
    finally:
        src_syn.close()
        src_prog.close()

    signaler("Écriture des exports", 95)
    from moteur import tracabilite
    resume = {
        "version_systeme": tracabilite.VERSION_SYSTEME,
        "calcule_le": tracabilite.maintenant(),
        "taille_carre_m": TAILLE_CARRE * 10,
        "nb_foyers": len(foyers),
        "dates": [entrees[0]["date"], entrees[-1]["date"]],
        "foyers": foyers,
        "hectares_confirmes_total": round(int((synthese == S.CONFIRMEE).sum()) * HECTARES_PAR_PIXEL, 2),
        "hectares_confirmes_foyers": round(sum(f["hectares_confirmes"] for f in foyers), 2),
        "nb_foyers_hors_validite": sum(1 for f in foyers if not f["dans_validite"]),
    }
    with open(sortie / "foyers.json", "w", encoding="utf-8") as f:
        json.dump(resume, f, ensure_ascii=False, indent=2)
    _ecrire_geojson(foyers, sortie / "foyers.geojson")
    _ecrire_kml(foyers, sortie / "foyers.kml")
    return resume


def _description(f):
    t = f["transition"]
    validite = ("dans la zone d'entraînement du modèle" if f.get("dans_validite")
                else "hors zone d'entraînement : résultat indicatif")
    return (f"Perte confirmée (U-Net + NDVI) : {f['hectares_confirmes']} ha ; non confirmée : "
            f"{f['hectares_un_indicateur']} ha ; transition principale : {t['de']} -> {t['vers']} "
            f"({t['part']} %) ; perte progressive : {f['part_progressive']} % des pixels en perte ; "
            f"{validite}")


def _ecrire_geojson(foyers, chemin):
    entites = [{
        "type": "Feature",
        "properties": {"numero": f["numero"], "hectares_confirmes": f["hectares_confirmes"],
                       "hectares_un_indicateur": f["hectares_un_indicateur"],
                       "transition": f"{f['transition']['de']} -> {f['transition']['vers']}",
                       "part_progressive": f["part_progressive"],
                       "dans_validite": f.get("dans_validite")},
        "geometry": {"type": "Polygon", "coordinates": [f["contour"] + [f["contour"][0]]]},
    } for f in foyers]
    with open(chemin, "w", encoding="utf-8") as fic:
        json.dump({"type": "FeatureCollection", "features": entites}, fic, ensure_ascii=False)


def _ecrire_kml(foyers, chemin):
    from xml.sax.saxutils import escape
    lignes = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<kml xmlns="http://www.opengis.net/kml/2.2"><Document>',
              "<name>Zones à surveiller</name>",
              '<Style id="z"><LineStyle><color>ff2828ff</color><width>2</width></LineStyle>'
              "<PolyStyle><fill>0</fill></PolyStyle></Style>"]
    for f in foyers:
        coords = " ".join(f"{x},{y},0" for x, y in f["contour"] + [f["contour"][0]])
        lignes.append(
            f"<Placemark><name>Zone {f['numero']}</name><description>{escape(_description(f))}"
            f"</description><styleUrl>#z</styleUrl><Polygon><outerBoundaryIs><LinearRing>"
            f"<coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>")
    lignes.append("</Document></kml>")
    Path(chemin).write_text("\n".join(lignes), encoding="utf-8")
