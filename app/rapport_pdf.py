"""
Rapports PDF (bibliothèque ReportLab) : un rapport par suivi temporel et
un rapport par analyse. Chaque rapport se termine par une partie
« Traçabilité » : version du système, empreinte du modèle, identifiant et
empreinte SHA-256 de chaque archive Sentinel-2, empreinte des fichiers produits.

Tous les chiffres viennent des fichiers de résultats (resume.json,
foyers.json) et de la base : rien n'est recalculé ni estimé ici.
"""
import io
import json
from datetime import datetime
from pathlib import Path

import numpy as np

import config

from app.indicateurs import AVIS, reponse_suivi, zones_du_suivi
from app.modeles_bd import bd
from moteur import tracabilite
from moteur.config_modele import CLASSES, COULEURS, EMPRISE_ENTRAINEMENT

# ---------------------------------------------------------------------
# Mise en page
# ---------------------------------------------------------------------
VERT, ENCRE, GRIS, FILET, FOND = "#1f6f4a", "#1b2420", "#5d6b64", "#d5dcd8", "#f2f5f3"
LIBELLES_SYNTHESE = {4: "Perte de végétation confirmée (U-Net + NDVI)", 1: "Perte non confirmée par le NDVI",
                     2: "Gain de végétation (>= 0,5 ha)", 0: "Pas de changement",
                     3: "Changement lié au lac", 5: "Petit gain (< 0,5 ha)"}

# Caractères absents des polices standard du PDF : remplacés
_REMPLACER = {"−": "-", "→": "->", "≥": ">=", "≤": "<=", "≈": "~", "\u202f": " ",
              "×": "x", "·": "-", "…": "..."}


def _polices():
    """Police avec accents : Arial (Windows) ou DejaVu (Linux), sinon Helvetica."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    candidats = [("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
                 ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]
    for normal, gras in candidats:
        if Path(normal).exists() and Path(gras).exists():
            try:
                pdfmetrics.registerFont(TTFont("Texte", normal))
                pdfmetrics.registerFont(TTFont("Texte-Gras", gras))
                return "Texte", "Texte-Gras", True
            except Exception:
                pass
    return "Helvetica", "Helvetica-Bold", False


class Mise:
    """Styles et petites fonctions de mise en page."""

    def __init__(self):
        from reportlab.lib.styles import ParagraphStyle
        self.normal, self.gras, self.unicode = _polices()
        base = dict(fontName=self.normal, fontSize=9.5, leading=13, textColor=ENCRE)
        self.p = ParagraphStyle("p", **base, spaceAfter=5)
        self.petit = ParagraphStyle("petit", **{**base, "fontSize": 8, "leading": 10.5,
                                                "textColor": GRIS})
        self.cellule = ParagraphStyle("cel", **{**base, "fontSize": 8.3, "leading": 10.5})
        self.h1 = ParagraphStyle("h1", fontName=self.gras, fontSize=17, leading=21,
                                 textColor=ENCRE, spaceAfter=4)
        self.h2 = ParagraphStyle("h2", fontName=self.gras, fontSize=12.5, leading=16,
                                 textColor=VERT, spaceBefore=12, spaceAfter=6)
        self.h3 = ParagraphStyle("h3", fontName=self.gras, fontSize=10, leading=13,
                                 textColor=ENCRE, spaceBefore=6, spaceAfter=3)
        self.grand = ParagraphStyle("grand", fontName=self.gras, fontSize=12, leading=16,
                                    textColor=ENCRE, spaceAfter=4)
        self.mono = ParagraphStyle("mono", fontName="Courier", fontSize=7, leading=9,
                                   textColor=ENCRE)

    def t(self, texte):
        """Texte sûr pour la police choisie, avec échappement XML."""
        from xml.sax.saxutils import escape
        texte = str(texte)
        if not self.unicode:
            for a, b in _REMPLACER.items():
                texte = texte.replace(a, b)
        else:
            texte = texte.replace("\u202f", " ")
        return escape(texte)

    def code(self, texte, coupe=40):
        """Identifiant long (empreinte, nom de produit) en police à chasse fixe,
        coupé en lignes de `coupe` caractères pour tenir dans sa colonne."""
        from reportlab.platypus import Paragraph
        from xml.sax.saxutils import escape
        texte = str(texte)
        morceaux = [escape(texte[i:i + coupe]) for i in range(0, len(texte), coupe)]
        return Paragraph("<br/>".join(morceaux), self.mono)

    def para(self, texte, style=None, brut=False):
        from reportlab.platypus import Paragraph
        return Paragraph(texte if brut else self.t(texte), style or self.p)

    def tableau(self, lignes, largeurs, entete=True, aligner_droite=()):
        """Tableau simple ; la première ligne est l'en-tête."""
        from reportlab.lib import colors
        from reportlab.platypus import Table, TableStyle
        cellules = [[c if not isinstance(c, (str, int, float)) else
                     self.para(c if isinstance(c, str) else nombre(c), self.cellule)
                     for c in ligne] for ligne in lignes]
        tab = Table(cellules, colWidths=largeurs, repeatRows=1 if entete else 0)
        style = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor(FILET)),
                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                 ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]
        if entete:
            style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(FOND)))
        tab.setStyle(TableStyle(style))
        return tab


def nombre(v, d=1):
    """Nombre au format français : espace pour les milliers, virgule décimale."""
    if v is None:
        return "—"
    if isinstance(v, int) or d == 0:
        return f"{round(v):,}".replace(",", " ")
    return f"{v:,.{d}f}".replace(",", " ").replace(".", ",")


def km2(ha):
    return nombre(ha / 100, 1 if ha < 10000 else 0) + " km²"


def _pied(nom_systeme):
    def dessiner(canvas, doc):
        from reportlab.lib.units import mm
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColorRGB(0.36, 0.42, 0.39)
        canvas.drawString(18 * mm, 10 * mm,
                          f"{nom_systeme} v{tracabilite.VERSION_SYSTEME} - rapport généré le "
                          f"{datetime.now().strftime('%d/%m/%Y %H:%M')}")
        canvas.drawRightString(192 * mm, 10 * mm, f"Page {doc.page}")
        canvas.restoreState()
    return dessiner


def _document(tampon, titre):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate
    return SimpleDocTemplate(tampon, pagesize=A4, title=titre, author=config.NOM_SYSTEME,
                             leftMargin=18 * mm, rightMargin=18 * mm,
                             topMargin=16 * mm, bottomMargin=18 * mm)


# ---------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------
def _image(pil, largeur_mm):
    from reportlab.lib.units import mm
    from reportlab.platypus import Image
    tampon = io.BytesIO()
    pil.convert("RGB").save(tampon, "JPEG", quality=85)
    tampon.seek(0)
    l, h = pil.size
    return Image(tampon, width=largeur_mm * mm, height=largeur_mm * mm * h / l)


def _superposer(chemin_calque, chemin_fond, cote=1400):
    """Calque PNG transparent posé sur l'image en couleurs réelles (ou sur un
    fond gris clair si elle manque), réduit à `cote` pixels au plus."""
    from PIL import Image
    calque = Image.open(chemin_calque).convert("RGBA")
    calque.thumbnail((cote, cote))
    if chemin_fond and Path(chemin_fond).exists():
        fond = Image.open(chemin_fond).convert("RGB").resize(calque.size)
        # fond légèrement éclairci pour que les couleurs du calque ressortent
        fond = Image.blend(fond, Image.new("RGB", fond.size, "white"), 0.35)
    else:
        fond = Image.new("RGB", calque.size, (236, 239, 237))
    fond.paste(calque, (0, 0), calque)
    return fond


def _legende(mise, elements):
    """Ligne de légende : pastilles de couleur et libellés."""
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import Table, TableStyle
    cellules, style = [], [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("LEFTPADDING", (0, 0), (-1, -1), 2)]
    for i, (couleur, texte) in enumerate(elements):
        cellules += ["", mise.para(texte, mise.petit)]
        style.append(("BACKGROUND", (2 * i, 0), (2 * i, 0), colors.HexColor(couleur)))
    largeurs = []
    for _ in elements:
        largeurs += [3.5 * mm, 170 * mm / len(elements) - 3.5 * mm]
    tab = Table([cellules], colWidths=largeurs, rowHeights=[5 * mm])
    tab.setStyle(TableStyle(style))
    return tab


def _courbe_ndvi(mise, dates, valeurs):
    """Courbe du NDVI médian par date (graphique vectoriel ReportLab)."""
    from reportlab.graphics.charts.lineplots import LinePlot
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    d = Drawing(170 * mm, 58 * mm)
    lp = LinePlot()
    lp.x, lp.y, lp.width, lp.height = 14 * mm, 9 * mm, 150 * mm, 42 * mm
    lp.data = [list(zip(range(len(valeurs)), valeurs))]
    lp.lines[0].strokeColor = colors.HexColor(VERT)
    lp.lines[0].strokeWidth = 1.6
    from reportlab.graphics.widgets.markers import makeMarker
    lp.lines[0].symbol = makeMarker("FilledCircle", size=3.5)
    bas, haut = min(valeurs), max(valeurs)
    marge = max((haut - bas) * 0.25, 0.01)
    lp.yValueAxis.valueMin, lp.yValueAxis.valueMax = round(bas - marge, 3), round(haut + marge, 3)
    lp.yValueAxis.labelTextFormat = lambda v: f"{v:.2f}".replace(".", ",")
    lp.xValueAxis.valueMin, lp.xValueAxis.valueMax = -0.3, len(valeurs) - 0.7
    lp.xValueAxis.valueSteps = list(range(len(valeurs)))
    lp.xValueAxis.labelTextFormat = lambda i: dates[int(round(i))][:4] if 0 <= round(i) < len(dates) else ""
    for axe in (lp.xValueAxis, lp.yValueAxis):
        axe.labels.fontName, axe.labels.fontSize = mise.normal, 7
        axe.strokeColor = colors.HexColor(FILET)
    lp.yValueAxis.visibleGrid = True
    lp.yValueAxis.gridStrokeColor = colors.HexColor("#e6ebe8")
    d.add(lp)
    d.add(String(14 * mm, 54 * mm, "NDVI médian hors du lac", fontName=mise.normal,
                 fontSize=7.5, fillColor=colors.HexColor(GRIS)))
    return d


# ---------------------------------------------------------------------
# Données communes
# ---------------------------------------------------------------------
def empreinte_archive(produit_id, chemin, connue=""):
    """SHA-256 de l'archive, calculé une fois puis gardé dans la base."""
    if connue:
        return connue
    if not Path(chemin).exists():
        return None
    valeur = tracabilite.sha256_fichier(chemin)
    bd().execute("UPDATE produit SET sha256 = ? WHERE id = ?", (valeur, produit_id))
    bd().commit()
    return valeur


def _analyses(numeros):
    marques = ",".join("?" * len(numeros))
    return bd().execute(
        "SELECT a.id, a.dossier, a.resume, a.cree_le, a.termine_le, p.id AS produit_id, p.nom, "
        "p.date_acquisition, p.tuile, p.chemin, p.sha256, p.version_traitement, p.satellite "
        f"FROM analyse a JOIN produit p ON p.id = a.produit_id WHERE a.id IN ({marques}) "
        "ORDER BY p.date_acquisition", numeros).fetchall()


def _modele_de(resumes):
    """Empreintes du modèle enregistrées par les analyses, et nombre
    d'analyses qui n'en ont pas (calculées avant la traçabilité)."""
    connues, sans = set(), 0
    for r in resumes:
        modele = (r.get("tracabilite") or {}).get("modele")
        if modele:
            connues.add(modele["sha256"])
        else:
            sans += 1
    return connues, sans


def _fichiers_produits(dossier, noms):
    lignes = []
    for nom in noms:
        chemin = Path(dossier) / nom
        if chemin.exists():
            lignes.append([nom, nombre(chemin.stat().st_size / 1024, 0) + " Ko",
                           tracabilite.sha256_fichier(chemin)])
    return lignes


def _bloc_tracabilite(mise, elements, resumes, produits, dossier, fichiers):
    """Partie « Traçabilité » commune aux deux rapports."""
    from reportlab.lib.units import mm
    elements.append(mise.para("Traçabilité", mise.h2))
    elements.append(mise.para(
        "Cette partie permet de vérifier, plus tard, que les résultats ont bien été obtenus à partir "
        "de ces images et de ce modèle. L'empreinte SHA-256 est une « signature » de 64 caractères : "
        "si un seul octet du fichier change, l'empreinte change complètement."))
    actuel = tracabilite.empreinte_modele()
    connues, sans = _modele_de(resumes)
    lignes = [["Élément", "Valeur"],
              ["Version du système", f"{config.NOM_SYSTEME} v{tracabilite.VERSION_SYSTEME}"],
              ["Fichier du modèle", actuel["fichier"] if actuel else "absent sur cette machine"],
              ["Empreinte du modèle (actuel)", mise.code(actuel["sha256"], 64) if actuel else "—"]]
    for empreinte in sorted(connues):
        lignes.append(["Empreinte du modèle enregistrée par les analyses", mise.code(empreinte, 64)])
    if connues:
        if actuel and connues != {actuel["sha256"]}:
            lignes.append(["Attention", "Le modèle a changé depuis le calcul de certaines analyses."])
    if sans:
        lignes.append(["Analyses sans empreinte enregistrée",
                       f"{sans} (calculées avant l'ajout de la traçabilité ; relancer l'analyse "
                       "pour l'enregistrer)"])
    elements.append(mise.tableau(lignes, [62 * mm, 112 * mm]))

    elements.append(mise.para("Images Sentinel-2 utilisées", mise.h3))
    lignes = [["Date", "Identifiant du produit (ESA)", "SHA-256 de l'archive"]]
    for p in produits:
        e = empreinte_archive(p["produit_id"], p["chemin"], p["sha256"])
        lignes.append([p["date_acquisition"], mise.code(p["nom"], 33),
                       mise.code(e, 33) if e else "archive introuvable"])
    elements.append(mise.tableau(lignes, [23 * mm, 76 * mm, 75 * mm]))

    if fichiers:
        elements.append(mise.para("Fichiers produits", mise.h3))
        lignes = [["Fichier", "Taille", "SHA-256"]] + [
            [n, t, mise.code(h, 64)] for n, t, h in _fichiers_produits(dossier, fichiers)]
        elements.append(mise.tableau(lignes, [38 * mm, 20 * mm, 116 * mm]))


# ---------------------------------------------------------------------
# Rapport d'un suivi temporel
# ---------------------------------------------------------------------
def rapport_suivi(suivi, utilisateur, nom_systeme):
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, PageBreak, Spacer

    resume = json.loads(suivi["resume"])
    rep = reponse_suivi(resume)
    numeros = [int(v) for v in suivi["analyses"].split(",")]
    analyses = _analyses(numeros)
    resumes = [json.loads(a["resume"]) for a in analyses]
    zones = zones_du_suivi(suivi)
    mise = Mise()
    e = []

    # --- En-tête ---
    zone = [float(v) for v in suivi["zone"].split(",")]
    e.append(mise.para("Rapport de suivi de la dégradation des terres", mise.h1))
    e.append(mise.para(
        f"Suivi n° {suivi['id']} - tuile {suivi['tuile']} - {resume['nb_dates']} images de "
        f"{resume['dates'][0]} à {resume['dates'][-1]} - zone ouest {zone[0]:.4f}, sud {zone[1]:.4f}, "
        f"est {zone[2]:.4f}, nord {zone[3]:.4f} (degrés)", mise.petit))
    e.append(mise.para(f"Préparé pour : {utilisateur['nom']} - système {nom_systeme}", mise.petit))
    e.append(Spacer(1, 6))

    # --- 1. Réponse ---
    e.append(mise.para("1. Réponse", mise.h2))
    e.append(mise.para(
        f"Question : y a-t-il une dégradation des terres dans cette zone entre "
        f"{resume['dates'][0][:4]} et {resume['dates'][-1][:4]} ?"))
    if rep:
        e.append(mise.para(rep["titre"], mise.grand))
        lignes = [["Indicateur", "Valeur"],
                  ["Surface analysée (état connu aux deux périodes)", km2(rep["zone_ha"])],
                  ["Pertes de végétation confirmées (U-Net + NDVI, >= 0,5 ha)",
                   f"{km2(rep['confirmee_ha'])} ({nombre(rep['part_degradation'], 2)} %)"],
                  ["Pertes non confirmées par le NDVI", km2(rep["non_confirmee_ha"])],
                  ["Gains de végétation (>= 0,5 ha)",
                   f"{km2(rep['amelioration_ha'])} ({nombre(rep['part_amelioration'])} %)"],
                  ["Terres nues hors du lac (Mann-Kendall)",
                   f"{rep['nues_sens']} (p = "
                   f"{'—' if rep['nues_p'] is None else ('< 0,001' if rep['nues_p'] < 0.001 else nombre(rep['nues_p'], 3))})"]]
        if rep["ndvi_debut"] is not None:
            p = rep["ndvi_p"]
            lignes.append(["NDVI médian, première et dernière date",
                           f"{nombre(rep['ndvi_debut'], 3)} puis {nombre(rep['ndvi_fin'], 3)} - "
                           f"tendance : {rep['ndvi_sens']} (p = "
                           f"{'< 0,001' if p is not None and p < 0.001 else nombre(p, 3)})"])
        e.append(mise.tableau(lignes, [95 * mm, 79 * mm]))
        e.append(mise.para(
            "Règle de la réponse : « oui » si les terres nues hors du lac augmentent significativement, ou si "
            "les pertes confirmées dépassent les gains avec une baisse significative du NDVI ; « non » si les "
            "pertes confirmées sont plus petites que les gains sans baisse du NDVI ni hausse des terres nues ; "
            "« mitigé » sinon. Une perte confirmée est un signal cohérent entre deux sources (modèle et NDVI), "
            "pas une vérification de terrain.", mise.petit))
        v = resume.get("validite") or {}
        if v:
            texte = {"valide": "Domaine de validité : toute la zone est dans la zone d'entraînement du modèle "
                               "(résultat validé).",
                     "partiel": f"Domaine de validité : {nombre(v['part_zone'])} % de la zone est dans la zone "
                                "d'entraînement du modèle ; le reste est indicatif.",
                     "indicatif": "Domaine de validité : la zone est hors de la zone d'entraînement du modèle ; "
                                  "le résultat est indicatif (non validé sur cette zone)."}[v["statut"]]
            e.append(mise.para(texte, mise.petit))
    else:
        e.append(mise.para("Synthèse indisponible pour ce suivi (NDVI manquant ou ancienne méthode)."))
    for a in resume.get("avertissements", []):
        e.append(mise.para("Attention : " + a, mise.petit))

    # --- 2. Données ---
    e.append(mise.para("2. Données utilisées", mise.h2))
    e.append(mise.para(
        "Images Sentinel-2 de niveau L2A (réflectance de surface), une par date. Chaque image a été "
        "classée par le modèle U-Net (analyse), puis les cartes ont été comparées."))
    lignes = [["Date", "Analyse", "Satellite", "Non classé*", "Hors validité"]]
    for a, r in zip(analyses, resumes):
        lignes.append([a["date_acquisition"], f"n° {a['id']}", a["satellite"],
                       f"{nombre(r['statistiques']['part_non_classee'])} %",
                       f"{nombre(r.get('part_hors_entrainement', 0))} %"])
    e.append(mise.tableau(lignes, [28 * mm, 24 * mm, 26 * mm, 48 * mm, 48 * mm]))
    e.append(mise.para("* Nuages, ombres et données manquantes (couche SCL de l'ESA).", mise.petit))

    # --- 3. Méthode ---
    e.append(mise.para("3. Méthode", mise.h2))
    k = len(resume["periode_debut"])
    for texte in [
        "Classification : U-Net, 14 canaux (10 bandes et 4 indices), 5 classes, 10 m.",
        f"Étape 1 - état dominant : pour les {k} premières et les {k} dernières dates, chaque pixel reçoit "
        f"l'état présent {'au moins 2 fois sur 3' if k == 3 else 'à la date'} : eau (classe 0), végétation "
        f"(classes 1 et 2) ou surface nue (classes 3 et 4) ; sinon il est indéterminé.",
        "Étape 2 - changement : végétation devenue surface nue = perte candidate ; surface nue devenue "
        "végétation = gain ; eau dans une seule période = changement lié au lac.",
        "Étape 3 - confirmation : une perte candidate est confirmée si le NDVI moyen de la période récente "
        "est inférieur de plus de 0,10 à celui de la période de référence (NDVI mesuré sur les images, "
        "indépendant du modèle), en taches d'au moins 0,5 ha (50 pixels, 4-connexité). Les gains sont "
        "retenus à partir de 0,5 ha. Règle identique au notebook v8 du mémoire.",
        f"Compléments : tendances des surfaces et du NDVI médian (Mann-Kendall au seuil de "
        f"{nombre(resume['niveau_signification'] * 100, 0)} %, pente de Sen) ; tendance du NDVI par pixel "
        f"(|S| >= {resume.get('seuil_s_pixel') or '—'} pour {resume['nb_dates']} dates).",
    ]:
        e.append(mise.para("- " + texte))

    # --- 4. Résultats : carte ---
    e.append(PageBreak())
    e.append(mise.para("4. Où se trouvent les changements ?", mise.h2))
    dossier = Path(suivi["dossier"])
    fond = Path(analyses[-1]["dossier"]) / "rvb.jpg"
    if not fond.exists():
        try:
            from moteur.apercu import ecrire_image_rvb
            ecrire_image_rvb(analyses[-1]["chemin"], resumes[-1]["zone_pixels"], fond)
        except Exception:
            pass
    if (dossier / "synthese.png").exists():
        e.append(_image(_superposer(dossier / "synthese.png", fond), 174))
        e.append(Spacer(1, 3))
        cats = resume["bilans"]["synthese"]["categories"]
        courts = {4: "Perte confirmée", 1: "Perte non confirmée", 2: "Gain >= 0,5 ha",
                  3: "Lié au lac", 5: "Petit gain"}
        e.append(_legende(mise, [(c["couleur"], courts[c["code"]]) for c in cats
                                 if c["code"] != 0]))
        e.append(mise.para(
            (f"Fond : image en couleurs réelles du {analyses[-1]['date_acquisition']} (éclaircie). "
             if fond.exists() else "") + "Les pixels sans changement ne sont pas colorés.", mise.petit))
        v = resume.get("validite") or {}
        dedans = {c["code"]: c["hectares"] for c in (v.get("synthese_dedans") or [])}
        if dedans and v.get("statut") != "valide":
            lignes = [["Catégorie", "Surface", "Part de la zone", "Dans la zone validée"]]
            for c in cats:
                lignes.append([LIBELLES_SYNTHESE[c["code"]], km2(c["hectares"]),
                               f"{nombre(c['pourcentage'])} %", km2(dedans.get(c["code"], 0))])
            largeurs = [70 * mm, 36 * mm, 32 * mm, 36 * mm]
        else:
            lignes = [["Catégorie", "Surface", "Part de la zone"]]
            for c in cats:
                lignes.append([LIBELLES_SYNTHESE[c["code"]], km2(c["hectares"]),
                               f"{nombre(c['pourcentage'])} %"])
            largeurs = [80 * mm, 47 * mm, 47 * mm]
        e.append(Spacer(1, 4))
        e.append(mise.tableau(lignes, largeurs))

    # --- 5. Évolution ---
    e.append(mise.para("5. Évolution des surfaces (hors du lac)", mise.h2))
    noms = resume["classes"]
    indices = [1, 2, 3, 4, 5]                         # sans l'eau ; 5 = terres nues
    lignes = [["Date"] + [noms[i].split(" (")[0] for i in indices]]
    for l in resume["serie_terre"]:
        lignes.append([l["date"]] + [km2(l["hectares"][i]) for i in indices])
    e.append(mise.tableau(lignes, [24 * mm] + [30 * mm] * 5))
    lignes = [["Classe", "Tendance (Mann-Kendall)", "p", "Pente de Sen"]]
    for t in resume["tendances_terre"]:
        if t:
            lignes.append([t["classe"], t["sens"],
                           "—" if t["p"] is None else ("< 0,001" if t["p"] < 0.001 else nombre(t["p"], 3)),
                           "—" if t["pente_ha_par_an"] is None else f"{nombre(t['pente_ha_par_an'], 1 if abs(t['pente_ha_par_an']) < 100 else 0)} ha/an"])
    e.append(Spacer(1, 6))
    e.append(mise.tableau(lignes, [62 * mm, 52 * mm, 22 * mm, 38 * mm]))
    if resume.get("ndvi"):
        e.append(Spacer(1, 6))
        e.append(_courbe_ndvi(mise, resume["dates"], resume["ndvi"]["medianes"]))

    # --- 6. Zones à surveiller ---
    e.append(PageBreak())
    e.append(mise.para("6. Zones à surveiller et vérification humaine", mise.h2))
    if zones is None:
        e.append(mise.para("Les zones à surveiller n'ont pas encore été calculées pour ce suivi."))
    else:
        res = zones["resultat"]
        e.append(mise.para(
            f"La zone est découpée en carrés de 1 km. Les {res['nb_foyers']} carrés non voisins qui "
            f"concentrent le plus de pertes de végétation confirmées sont retenus : ils contiennent "
            f"{nombre(res['hectares_confirmes_foyers'])} ha sur {nombre(res['hectares_confirmes_total'])} ha "
            f"de pertes confirmées. Chaque zone a été examinée visuellement sur les images en "
            f"couleurs réelles de la première et de la dernière date."))
        c = zones["comptes"]
        if zones["verifies"]:
            ic = zones["intervalle"]
            e.append(mise.para(
                f"Avis enregistrés : {zones['verifies']} sur {zones['total']} - dégradation visible : "
                f"{c['visible']}, incertain : {c['incertain']}, pas de dégradation visible : "
                f"{c['non_visible']}. Part « visible » : {nombre(zones['part_visible'])} % "
                f"(intervalle de confiance à 95 %, méthode de Wilson : {nombre(ic[0])} à {nombre(ic[1])} %).",
                ))
            e.append(mise.para(
                "Cette vérification est visuelle, faite par une personne qui connaissait le résultat du "
                "modèle ; elle ne remplace pas une vérification de terrain.", mise.petit))
        lignes = [["Zone", "Centre (lon, lat)", "Confirmée", "Transition principale", "Avis"]]
        for f in res["foyers"]:
            avis = zones["avis"].get(f["numero"])
            texte_avis = AVIS[avis["avis"]] if avis else "non vérifiée"
            if avis and avis["commentaire"]:
                texte_avis += f" - « {avis['commentaire']} »"
            t = f["transition"]
            lignes.append([str(f["numero"]), f"{f['centre'][0]:.4f}, {f['centre'][1]:.4f}",
                           f"{nombre(f['hectares_confirmes'])} ha",
                           f"{t['de']} vers {t['vers']} ({nombre(t['part'])} %)"
                           + ("" if f.get("dans_validite", True) else " - indicatif"), texte_avis])
        e.append(mise.tableau(lignes, [12 * mm, 32 * mm, 20 * mm, 58 * mm, 52 * mm]))

        # Images avant / après
        dossier_zones = dossier / "foyers"
        from PIL import Image as PILImage
        from reportlab.platypus import Table
        for f in res["foyers"]:
            images = {i["rang"]: i for i in f["images"]}
            if "avant" not in images or "apres" not in images:
                continue
            cel = []
            for rang in ("avant", "apres"):
                img = PILImage.open(dossier_zones / images[rang]["fichier"]).convert("RGB")
                contour = dossier_zones / f"foyer_{f['numero']:02d}_contour.png"
                if rang == "apres" and contour.exists():
                    calque = PILImage.open(contour).convert("RGBA").resize(img.size)
                    img.paste(calque, (0, 0), calque)
                cel.append(_image(img, 70))
            bloc = [mise.para(f"Zone {f['numero']} - {images['avant']['date']} (gauche) et "
                              f"{images['apres']['date']} (droite, contour rouge : perte confirmée)",
                              mise.h3),
                    Table([cel], colWidths=[87 * mm, 87 * mm])]
            e.append(KeepTogether(bloc))

    # --- 7. Limites ---
    e.append(mise.para("7. Limites", mise.h2))
    for texte in _limites():
        e.append(mise.para("- " + texte))

    # --- Traçabilité ---
    e.append(PageBreak())
    trace = resume.get("tracabilite") or {}
    if trace:
        e.append(mise.para(f"Suivi calculé le {trace.get('calcule_le', '—')} avec {config.NOM_SYSTEME} "
                           f"v{trace.get('version_systeme', '—')} (méthode version {resume['version']}).",
                           mise.petit))
    _bloc_tracabilite(mise, e, resumes, analyses, dossier,
                      ["synthese.tif", "occupation.tif", "progression.tif", "productivite.tif",
                       "ndvi_pente.tif", "serie.csv", "transitions.csv", "resume.json",
                       "foyers/foyers.json", "foyers/foyers.geojson"])

    tampon = io.BytesIO()
    doc = _document(tampon, f"Rapport du suivi n° {suivi['id']}")
    doc.build(e, onFirstPage=_pied(nom_systeme), onLaterPages=_pied(nom_systeme))
    return tampon.getvalue()


def _limites():
    chemin = Path(__file__).resolve().parents[1] / "moteur" / "evaluation_modele.json"
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)["limites"] + [
            "Une perte détectée est un changement cohérent entre images ; sa cause (sécheresse, "
            "culture, pâturage, variation du lac) n'est pas identifiée par le système.",
            "En dehors de la zone d'entraînement du modèle, les résultats sont indicatifs : la précision "
            "du modèle n'y a pas été mesurée."]


# ---------------------------------------------------------------------
# Rapport d'une analyse
# ---------------------------------------------------------------------
def rapport_analyse(analyse, utilisateur, nom_systeme):
    from reportlab.lib.units import mm
    from reportlab.platypus import Spacer, Table

    resume = json.loads(analyse["resume"])
    mise = Mise()
    e = []
    produit = bd().execute("SELECT *, id AS produit_id FROM produit WHERE id = ?",
                           (analyse["produit_id"],)).fetchone()
    dossier = Path(analyse["dossier"])

    e.append(mise.para("Rapport d'analyse : occupation du sol", mise.h1))
    e.append(mise.para(
        f"Analyse n° {analyse['id']} - image du {resume['date']} - tuile {resume['tuile']} - "
        f"{nombre(resume['surface_km2'])} km² - {resume['saison']}", mise.petit))
    e.append(mise.para(f"Préparé pour : {utilisateur['nom']} - système {nom_systeme}", mise.petit))
    e.append(Spacer(1, 6))

    e.append(mise.para("1. Carte d'occupation du sol", mise.h2))
    cel = []
    if (dossier / "rvb.jpg").exists():
        from PIL import Image as PILImage
        img = PILImage.open(dossier / "rvb.jpg")
        img.thumbnail((1000, 1000))
        cel.append(_image(img, 84))
    if (dossier / "carte.png").exists():
        cel.append(_image(_superposer(dossier / "carte.png", None, 1000), 84))
    if cel:
        e.append(Table([cel], colWidths=[87 * mm] * len(cel)))
        e.append(mise.para("Gauche : couleurs réelles (B4, B3, B2). Droite : classes prédites par le "
                           "modèle U-Net.", mise.petit))
        e.append(_legende(mise, list(zip(COULEURS, CLASSES))))

    e.append(mise.para("2. Surfaces par classe", mise.h2))
    st = resume["statistiques"]
    lignes = [["Classe", "Surface", "Part des pixels classés"]]
    for c in st["classes"]:
        lignes.append([c["classe"], f"{nombre(c['hectares'])} ha", f"{nombre(c['pourcentage'], 2)} %"])
    e.append(mise.tableau(lignes, [80 * mm, 47 * mm, 47 * mm]))
    e.append(mise.para(f"Part non classée (nuages, données manquantes) : "
                       f"{nombre(st['part_non_classee'], 2)} %.", mise.petit))
    if resume.get("statistiques_validite"):
        sv = resume["statistiques_validite"]
        e.append(mise.para("Surfaces dans la zone de validité du modèle", mise.h3))
        lignes = [["Classe", "Surface", "Part"]]
        for c in sv["classes"]:
            lignes.append([c["classe"], f"{nombre(c['hectares'])} ha", f"{nombre(c['pourcentage'], 2)} %"])
        e.append(mise.tableau(lignes, [80 * mm, 47 * mm, 47 * mm]))
    for a in resume.get("avertissements", []):
        e.append(mise.para("Attention : " + a, mise.petit))

    e.append(mise.para("3. Lecture et limites", mise.h2))
    o, s, est, n = EMPRISE_ENTRAINEMENT
    e.append(mise.para(
        "Cette carte décrit l'état de la surface à une date. Elle ne démontre pas à elle seule une "
        "désertification : celle-ci s'évalue en comparant plusieurs dates de la même saison (suivi "
        f"temporel). Zone de validité du modèle : ouest {o}, sud {s}, est {est}, nord {n} (degrés)."))
    for texte in _limites():
        e.append(mise.para("- " + texte))

    trace = resume.get("tracabilite") or {}
    e.append(Spacer(1, 6))
    if trace:
        e.append(mise.para(f"Analyse calculée le {trace.get('calcule_le')} avec {config.NOM_SYSTEME} "
                           f"v{trace.get('version_systeme')}.", mise.petit))
    _bloc_tracabilite(mise, e, [resume], [produit], dossier,
                      ["classes.tif", "statistiques.csv", "resume.json"])

    tampon = io.BytesIO()
    doc = _document(tampon, f"Rapport de l'analyse n° {analyse['id']}")
    doc.build(e, onFirstPage=_pied(nom_systeme), onLaterPages=_pied(nom_systeme))
    return tampon.getvalue()
