"""
Validation de la détection — étape 1 : tirage des points de contrôle.

Tire au hasard des pixels dans chaque catégorie de la carte de synthèse d'un
suivi (échantillonnage stratifié, Olofsson et al., 2014), puis prépare tout ce
qu'il faut pour les vérifier visuellement, SANS montrer ce que le système a
détecté (contrôle « en aveugle ») :

  donnees/validation/suivi_<N>/
    carnet.html            page à ouvrir dans le navigateur : images avant / après,
                           questions, export des réponses (reponses.csv)
    points.kml             les mêmes points pour Google Earth Pro (images d'archive)
    points.csv             numéro, longitude, latitude
    tirage.json            paramètres du tirage et surface de chaque catégorie
    cle_NE_PAS_OUVRIR.csv  catégorie de chaque point : à n'ouvrir qu'après le contrôle

Usage (depuis le dossier du projet) :
    python scripts/validation_tirage.py --suivi 4
    python scripts/validation_tirage.py --suivi 4 --par-categorie 25 --graine 7

Ensuite : remplir le carnet, exporter reponses.csv dans le même dossier, puis
    python scripts/validation_resultats.py --suivi 4
"""
import argparse
import base64
import csv
import io
import json
import random
import sqlite3
import sys
from pathlib import Path

import numpy as np

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))

from moteur import serie as S  # noqa: E402

# Catégories échantillonnées (codes de synthese.tif, méthode version 3)
STRATES = [
    (S.CONFIRMEE, "perte_confirmee"),
    (S.DEGRADATION, "perte_non_confirmee"),
    (S.AMELIORATION, "gain"),
    (S.STABLE, "stable"),
]
DEMI_FENETRE = 32          # vignette de 64 x 64 pixels (640 m)
MARGE = DEMI_FENETRE       # pas de point au bord de la zone
AGRANDIR = 4


# ---------------------------------------------------------------------
# Accès aux données (isolés pour les tests)
# ---------------------------------------------------------------------
def lire_suivi(chemin_base, suivi_id):
    connexion = sqlite3.connect(chemin_base)
    connexion.row_factory = sqlite3.Row
    ligne = connexion.execute("SELECT dossier, statut, resume FROM suivi WHERE id = ?",
                              (suivi_id,)).fetchone()
    connexion.close()
    if ligne is None:
        sys.exit(f"Suivi n° {suivi_id} introuvable.")
    if ligne["statut"] != "terminee" or not ligne["resume"]:
        sys.exit(f"Le suivi n° {suivi_id} n'est pas terminé.")
    resume = json.loads(ligne["resume"])
    if resume.get("version") != S.VERSION:
        sys.exit("Ce suivi a été calculé avec une ancienne méthode : cliquez sur « Recalculer » "
                 "dans la page du suivi, puis relancez ce script.")
    return Path(ligne["dossier"]), resume


def lire_synthese(chemin):
    src = S._ouvrir(chemin)
    try:
        return S._lire_lignes(src, 0, src.height), src.transform, src.crs
    finally:
        src.close()


def vers_wgs84(transform, crs, lignes, colonnes):
    from rasterio.warp import transform as transformer
    xs, ys = [], []
    for l, c in zip(lignes, colonnes):
        x, y = transform * (c + 0.5, l + 0.5)
        xs.append(x)
        ys.append(y)
    lon, lat = transformer(crs, "EPSG:4326", xs, ys)
    return list(zip(lon, lat))


def vignette_rvb(archive, fenetre):
    from moteur.foyers import lire_rvb
    return lire_rvb(archive, fenetre)


# ---------------------------------------------------------------------
# Calculs
# ---------------------------------------------------------------------
def tirer_points(synthese, par_categorie, graine, fenetre=None, bloc=256):
    """Tirage aléatoire simple dans chaque catégorie, à l'intérieur de `fenetre`
    (r0, r1, c0, c1 : zone d'entraînement du modèle) et à plus de MARGE pixels du bord.

    Les pixels sont comptés puis retrouvés par bandes de lignes : aucune liste de
    tous les pixels n'est créée (une tuile entière en demanderait plus de 1 Go).
    Les surfaces renvoyées (poids des strates) portent sur la même zone que le tirage."""
    rng = np.random.default_rng(graine)
    H, L = synthese.shape
    r0, r1, c0, c1 = fenetre if fenetre is not None else (0, H, 0, L)
    r0, r1 = max(r0, MARGE), min(r1, H - MARGE)
    c0, c1 = max(c0, MARGE), min(c1, L - MARGE)
    if r1 <= r0 or c1 <= c0:
        return [], {nom: 0 for _, nom in STRATES}
    zone = synthese[r0:r1, c0:c1]
    debuts = list(range(0, zone.shape[0], bloc))
    points, surfaces = [], {}
    for code, nom in STRATES:
        par_bande = np.array([int((zone[b:b + bloc] == code).sum()) for b in debuts], dtype=np.int64)
        total = int(par_bande.sum())
        surfaces[nom] = total
        if total == 0:
            continue
        rangs = np.sort(rng.choice(total, size=min(par_categorie, total), replace=False))
        cumul = np.concatenate([[0], np.cumsum(par_bande)])
        for b_index in np.unique(np.searchsorted(cumul, rangs, side="right") - 1):
            b = debuts[b_index]
            dans = rangs[(rangs >= cumul[b_index]) & (rangs < cumul[b_index + 1])] - cumul[b_index]
            positions = np.flatnonzero(zone[b:b + bloc] == code)[dans]
            for pos in positions:
                l, c = divmod(int(pos), zone.shape[1])
                points.append({"strate": nom, "code": int(code),
                               "ligne": r0 + b + l, "colonne": c0 + c})
    random.Random(graine).shuffle(points)            # ordre mélangé : aveugle
    for i, p in enumerate(points, start=1):
        p["id"] = f"P{i:03d}"
    return points, surfaces


def marquer(rvb):
    """Agrandit la vignette et dessine une croix ouverte autour du pixel central."""
    img = np.repeat(np.repeat(rvb, AGRANDIR, axis=0), AGRANDIR, axis=1).copy()
    c = DEMI_FENETRE * AGRANDIR + AGRANDIR // 2
    rouge = np.array([255, 40, 40], dtype=np.uint8)
    for d in range(3 * AGRANDIR, 7 * AGRANDIR):
        for y, x in ((c - d, c), (c + d, c), (c, c - d), (c, c + d)):
            if 0 <= y < img.shape[0] and 0 <= x < img.shape[1]:
                img[max(0, y - 1):y + 1, max(0, x - 1):x + 1] = rouge
    return img


def en_jpeg_base64(img):
    from PIL import Image
    tampon = io.BytesIO()
    Image.fromarray(img).save(tampon, format="JPEG", quality=85)
    return base64.b64encode(tampon.getvalue()).decode("ascii")


# ---------------------------------------------------------------------
# Fichiers de sortie
# ---------------------------------------------------------------------
def ecrire_kml(points, chemin):
    lignes = ['<?xml version="1.0" encoding="UTF-8"?>',
              '<kml xmlns="http://www.opengis.net/kml/2.2"><Document><name>Points de contrôle</name>']
    for p in points:
        lignes.append(f"<Placemark><name>{p['id']}</name><Point><coordinates>"
                      f"{p['lon']:.6f},{p['lat']:.6f},0</coordinates></Point></Placemark>")
    lignes.append("</Document></kml>")
    Path(chemin).write_text("\n".join(lignes), encoding="utf-8")


PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Carnet de contrôle - suivi __SUIVI__</title>
<style>
body{font-family:system-ui,Segoe UI,Arial,sans-serif;margin:0;background:#f4f5f2;color:#1b2420}
header{background:#1f6f4a;color:#fff;padding:14px 20px;position:sticky;top:0;z-index:2}
header h1{font-size:18px;margin:0 0 4px}header p{margin:0;font-size:13px}
main{max-width:1100px;margin:auto;padding:16px}
.aide{background:#fff;border-radius:10px;padding:12px 16px;margin-bottom:16px;font-size:14px;line-height:1.5}
.point{background:#fff;border-radius:10px;padding:14px 16px;margin-bottom:16px}
.point.fait{outline:2px solid #1f6f4a}
.point h2{font-size:16px;margin:0 0 8px}
.ligne{display:flex;gap:8px;flex-wrap:wrap;align-items:flex-start;margin-bottom:8px}
.ligne figure{margin:0;text-align:center;font-size:12px;color:#5d6b64}
.ligne img{width:192px;height:192px;border-radius:6px;display:block}
.periode{font-weight:600;font-size:13px;width:110px;padding-top:80px}
fieldset{border:1px solid #d5dcd8;border-radius:8px;margin:6px 0;padding:6px 10px;font-size:14px}
label{margin-right:14px;white-space:nowrap}
textarea{width:100%;box-sizing:border-box;font:inherit;font-size:13px}
button{background:#1f6f4a;color:#fff;border:0;border-radius:8px;padding:9px 16px;font-size:14px;cursor:pointer}
.compte{font-weight:600}
</style></head><body>
<header><h1>Carnet de contrôle en aveugle - suivi n° __SUIVI__</h1>
<p><span class="compte" id="compte"></span> - les réponses sont gardées dans ce navigateur.
<button id="exporter">Exporter reponses.csv</button></p></header>
<main>
<div class="aide"><b>Comment répondre.</b> Pour chaque point, regardez le pixel au centre de la croix rouge
(carré de 10 m). Les images du haut sont celles de la période de référence, celles du bas la période récente.
Indiquez ce qui domine sous la croix dans chaque période. Si les images ne suffisent pas, ouvrez points.kml dans
Google Earth Pro et utilisez l'historique des images (icône horloge). Répondez « illisible » plutôt que de deviner.
Le système ne vous dit pas ce qu'il a détecté : c'est voulu, pour ne pas influencer votre jugement.</div>
<div id="points"></div>
</main>
<script>
const POINTS = __POINTS__;
const CLE = "carnet_validation___SUIVI__";
let rep = {};
try { rep = JSON.parse(localStorage.getItem(CLE) || "{}"); } catch (e) { rep = {}; }
function sauver(){ try { localStorage.setItem(CLE, JSON.stringify(rep)); } catch (e) {} compter(); }
function compter(){
  const n = POINTS.filter(p => rep[p.id] && rep[p.id].reference && rep[p.id].recente).length;
  document.getElementById("compte").textContent = n + " / " + POINTS.length + " points renseignés";
  POINTS.forEach(p => { const d = document.getElementById(p.id);
    if (d) d.classList.toggle("fait", !!(rep[p.id] && rep[p.id].reference && rep[p.id].recente)); });
}
const ETATS = [["vegetation","Végétation"],["surface_nue","Sol nu / sable"],["eau","Eau"],["illisible","Illisible"]];
function radios(p, champ){
  return ETATS.map(([v,t]) => '<label><input type="radio" name="'+p.id+'_'+champ+'" value="'+v+'"'+
    ((rep[p.id]||{})[champ]===v?' checked':'')+'> '+t+'</label>').join("");
}
function images(liste){
  return liste.map(i => '<figure>'+(i.jpg?'<img src="data:image/jpeg;base64,'+i.jpg+'" alt="">':'<div style="width:192px;height:192px;background:#ddd;border-radius:6px"></div>')+i.date+'</figure>').join("");
}
const zone = document.getElementById("points");
zone.innerHTML = POINTS.map(p => '<section class="point" id="'+p.id+'"><h2>Point '+p.id+
  ' <small style="font-weight:400;color:#5d6b64">('+p.lat.toFixed(5)+' N, '+p.lon.toFixed(5)+' E)</small></h2>'+
  '<div class="ligne"><div class="periode">Référence</div>'+images(p.avant)+'</div>'+
  '<div class="ligne"><div class="periode">Récente</div>'+images(p.apres)+'</div>'+
  '<fieldset><b>Période de référence :</b> '+radios(p,"reference")+'</fieldset>'+
  '<fieldset><b>Période récente :</b> '+radios(p,"recente")+'</fieldset>'+
  '<fieldset><b>Confiance :</b> <label><input type="radio" name="'+p.id+'_confiance" value="sure"'+((rep[p.id]||{}).confiance==="sure"?' checked':'')+'> sûre</label>'+
  '<label><input type="radio" name="'+p.id+'_confiance" value="incertaine"'+((rep[p.id]||{}).confiance==="incertaine"?' checked':'')+'> incertaine</label></fieldset>'+
  '<textarea rows="1" placeholder="Commentaire (facultatif)" data-id="'+p.id+'">'+(((rep[p.id]||{}).commentaire)||"")+'</textarea></section>').join("");
zone.addEventListener("change", e => {
  const t = e.target; if (t.type !== "radio") return;
  const [id, champ] = t.name.split("_"); rep[id] = rep[id] || {}; rep[id][champ] = t.value; sauver();
});
zone.addEventListener("input", e => {
  const t = e.target; if (t.tagName !== "TEXTAREA") return;
  rep[t.dataset.id] = rep[t.dataset.id] || {}; rep[t.dataset.id].commentaire = t.value; sauver();
});
document.getElementById("exporter").addEventListener("click", () => {
  const q = s => '"' + String(s || "").replace(/"/g, '""') + '"';
  const lignes = ["id;reference;recente;confiance;commentaire"].concat(POINTS.map(p => {
    const r = rep[p.id] || {}; return [p.id, r.reference||"", r.recente||"", r.confiance||"", q(r.commentaire)].join(";"); }));
  const blob = new Blob(["\\ufeff" + lignes.join("\\n")], {type: "text/csv;charset=utf-8"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "reponses.csv"; a.click();
});
compter();
</script></body></html>"""


def main():
    import config
    parser = argparse.ArgumentParser(description="Tirage des points de contrôle d'un suivi")
    parser.add_argument("--suivi", type=int, required=True, help="numéro du suivi")
    parser.add_argument("--par-categorie", type=int, default=20, help="points par catégorie (20)")
    parser.add_argument("--graine", type=int, default=2026, help="graine du tirage (reproductible)")
    parser.add_argument("--base", default=str(config.CHEMIN_BASE))
    parser.add_argument("--toute-la-zone", action="store_true",
                        help="tirer aussi hors de la zone d'entraînement du modèle (contrôle indicatif)")
    args = parser.parse_args()

    from app.taches import entrees_suivi
    dossier_suivi, resume = lire_suivi(args.base, args.suivi)
    entrees = entrees_suivi(args.base, args.suivi)
    par_date = {e["date"]: e for e in entrees}
    sortie = config.DOSSIER_DONNEES / "validation" / f"suivi_{args.suivi}"
    sortie.mkdir(parents=True, exist_ok=True)

    print("Lecture de la carte de synthèse...")
    synthese, transform, crs = lire_synthese(dossier_suivi / "synthese.tif")
    fenetre = None
    if not args.toute_la_zone:
        src = S._ouvrir(dossier_suivi / "synthese.tif")
        try:
            fenetre = S._fenetre_validite(src)
        finally:
            src.close()
        if fenetre is None:
            sys.exit("Ce suivi ne recouvre pas la zone d'entraînement du modèle : "
                     "relancez avec --toute-la-zone pour un contrôle indicatif.")
        r0, r1, c0, c1 = fenetre
        print(f"Tirage limité à la zone d'entraînement du modèle : {(r1 - r0) * (c1 - c0) * S.HECTARES_PAR_PIXEL / 100:,.0f} km²"
              .replace(",", " "))
    points, surfaces = tirer_points(synthese, args.par_categorie, args.graine, fenetre)
    if not points:
        sys.exit("Aucun pixel à tirer : la carte de synthèse est vide.")
    coords = vers_wgs84(transform, crs, [p["ligne"] for p in points], [p["colonne"] for p in points])
    for p, (lon, lat) in zip(points, coords):
        p["lon"], p["lat"] = lon, lat
    print(f"{len(points)} points tirés : " + ", ".join(
        f"{nom} {sum(1 for p in points if p['strate'] == nom)}" for _, nom in STRATES))

    # Vignettes : chaque date des deux périodes
    debut, fin = resume["periode_debut"], resume["periode_fin"]
    publics, manquantes = [], {}
    for i, p in enumerate(points, start=1):
        print(f"  vignettes {i}/{len(points)}", end="\r", flush=True)
        groupes = {}
        for nom, dates in (("avant", debut), ("apres", fin)):
            groupes[nom] = []
            for d in dates:
                e = par_date.get(d)
                jpg = None
                if e is not None:
                    lz, cz = e["zone_pixels"][0], e["zone_pixels"][1]
                    l0, c0 = p["ligne"] - DEMI_FENETRE, p["colonne"] - DEMI_FENETRE
                    try:
                        rvb = vignette_rvb(e["archive"], (lz + l0, cz + c0,
                                                          lz + l0 + 2 * DEMI_FENETRE,
                                                          cz + c0 + 2 * DEMI_FENETRE))
                        jpg = en_jpeg_base64(marquer(rvb))
                    except Exception as erreur:          # archive absente ou illisible
                        manquantes.setdefault(d, str(erreur))
                groupes[nom].append({"date": d, "jpg": jpg})
        publics.append({"id": p["id"], "lon": round(p["lon"], 6), "lat": round(p["lat"], 6),
                        "avant": groupes["avant"], "apres": groupes["apres"]})
    print()
    for d, erreur in manquantes.items():
        print(f"  Attention : images du {d} indisponibles ({erreur}). Les cases resteront grises.")

    page = (PAGE.replace("__SUIVI__", str(args.suivi))
                .replace("__POINTS__", json.dumps(publics, ensure_ascii=False)))
    (sortie / "carnet.html").write_text(page, encoding="utf-8")
    ecrire_kml(points, sortie / "points.kml")
    with open(sortie / "points.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "longitude", "latitude"])
        for p in points:
            w.writerow([p["id"], f"{p['lon']:.6f}", f"{p['lat']:.6f}"])
    with open(sortie / "cle_NE_PAS_OUVRIR.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["id", "strate", "code", "ligne", "colonne"])
        for p in points:
            w.writerow([p["id"], p["strate"], p["code"], p["ligne"], p["colonne"]])
    with open(sortie / "tirage.json", "w", encoding="utf-8") as f:
        json.dump({"suivi": args.suivi, "graine": args.graine, "par_categorie": args.par_categorie,
                   "pixels_par_strate": surfaces, "hectares_par_pixel": S.HECTARES_PAR_PIXEL,
                   "fenetre_tirage": list(fenetre) if fenetre else None,
                   "periode_debut": debut, "periode_fin": fin, "nb_points": len(points)},
                  f, ensure_ascii=False, indent=2)
    print(f"Fichiers écrits dans {sortie}")
    print("Ouvrez carnet.html dans votre navigateur. N'ouvrez pas cle_NE_PAS_OUVRIR.csv avant la fin.")


if __name__ == "__main__":
    main()
