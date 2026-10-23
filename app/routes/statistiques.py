"""Statistiques et tendances : analyse quantitative des suivis temporels.

Toutes les valeurs viennent du fichier resume.json de chaque suivi
(calculé par moteur/serie.py) : surfaces par date, tests de Mann-Kendall,
pentes de Sen, matrice de transition et NDVI."""
import json

from flask import Blueprint, g, render_template, request

from app import graphiques
from app.indicateurs import VERSION_METHODE, reponse_suivi
from app.modeles_bd import bd
from app.routes.auth import connexion_requise

bp = Blueprint("statistiques", __name__)

COULEUR_TERRES_NUES = "#b98b4e"
COULEUR_VEGETATION = "#2e7d32"


def _suivis(uid):
    lignes = bd().execute(
        "SELECT * FROM suivi WHERE utilisateur_id = ? AND statut = 'terminee' ORDER BY id DESC",
        (uid,)).fetchall()
    resultat = []
    for s in lignes:
        r = json.loads(s["resume"])
        if r.get("version") == VERSION_METHODE:
            resultat.append((s, r))
    return resultat


def _tendance(t):
    """Version courte d'une tendance pour le tableau de comparaison."""
    if not t:
        return None
    return {"sens": t["sens"], "p": t["p"], "pente": t["pente_ha_par_an"]}


def _mois(dates):
    noms = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
            "septembre", "octobre", "novembre", "décembre"]
    return ", ".join(sorted({noms[int(d[5:7]) - 1] for d in dates}, key=lambda m: noms.index(m)))


@bp.route("/statistiques")
@connexion_requise
def page():
    from moteur.config_modele import CLASSES, COULEURS
    suivis = _suivis(g.utilisateur["id"])
    if not suivis:
        return render_template("statistiques.html", suivis=[], choisi=None)
    try:
        numero = int(request.args.get("suivi", suivis[0][0]["id"]))
    except ValueError:
        numero = suivis[0][0]["id"]
    choisi, resume = next(((s, r) for s, r in suivis if s["id"] == numero), suivis[0])
    vue = "zone" if request.args.get("vue") == "zone" else "terre"

    couleurs = list(COULEURS) + [COULEUR_TERRES_NUES, COULEUR_VEGETATION]
    serie = resume["serie"] if vue == "zone" else resume["serie_terre"]
    debut_classe = 0 if vue == "zone" else 1
    annees = [d[:4] for d in resume["dates"]]
    indices = list(range(debut_classe, len(resume["classes"])))

    # 1. Évolution des surfaces (km²), une courbe par classe
    graphe = graphiques.courbes(
        [{"nom": resume["classes"][c], "couleur": couleurs[c],
          "valeurs": [round(l["hectares"][c] / 100, 2) for l in serie],
          "pointille": c >= len(CLASSES)} for c in indices],
        annees, unite="km²", decimales=1, largeur=760, hauteur=300)

    # 2. Tendances
    tendances = resume["tendances"] if vue == "zone" else resume["tendances_terre"]
    tendances = [(c, tendances[c]) for c in indices if tendances[c]]

    # 3. Transitions : matrice, gains et pertes par classe
    m = resume["transitions"]
    n = len(CLASSES)
    bilan_classes = []
    for c in range(n):
        perdu = sum(m[c][b] for b in range(n) if b != c)
        gagne = sum(m[a][c] for a in range(n) if a != c)
        bilan_classes.append({"nom": CLASSES[c], "couleur": COULEURS[c], "stable": m[c][c],
                              "perdu": perdu, "gagne": gagne, "net": gagne - perdu})
    maximum = max((v for ligne in m for v in ligne), default=0)

    # 4. NDVI
    ndvi = resume.get("ndvi")
    graphe_ndvi = graphiques.courbes(
        [{"nom": "NDVI médian", "couleur": "#1c8457", "valeurs": ndvi["medianes"]}],
        annees, decimales=3, depuis_zero=False, aire=True, largeur=700) if ndvi else None

    # 5. Comparaison de tous les suivis (par exemple : février, mai, septembre)
    comparaison = []
    for s, r in suivis:
        tt, tz = r["tendances_terre"], r["tendances"]
        rep = reponse_suivi(r)
        comparaison.append({
            "suivi": s, "periode": f"{r['dates'][0][:4]}–{r['dates'][-1][:4]}",
            "mois": _mois(r["dates"]), "nb": r["nb_dates"],
            "terres_nues": _tendance(tt[5]),
            "veg_saine": _tendance(tt[1]), "veg_clairsemee": _tendance(tt[2]),
            "eau": _tendance(tz[0]),
            "ndvi": ({"sens": rep["ndvi_sens"], "p": r["ndvi"]["p"], "pente": r["ndvi"].get("pente_par_an")}
                     if rep and r.get("ndvi") else None),
            "degradation": rep["part_degradation"] if rep else None,
            "confirmee_ha": rep["confirmee_ha"] if rep else None,
            "amelioration": rep["part_amelioration"] if rep else None,
        })

    # 6. Barres empilées par année (maquette 7), filtre sur une classe
    try:
        classe = int(request.args.get("classe", -1))
    except ValueError:
        classe = -1
    choix_classes = [c for c in indices if c < len(CLASSES)]
    empilees = [c for c in choix_classes if classe in (-1, c)]
    barres = graphiques.barres_empilees(
        [{"nom": CLASSES[c], "couleur": couleurs[c], "valeurs": [round(l["hectares"][c] / 100, 2) for l in serie]}
         for c in empilees], annees, unite="km²", decimales=1, largeur=760, hauteur=300)

    # 7. Cartes comparatives : deux dates du suivi, classes et surfaces
    numeros = [int(v) for v in choisi["analyses"].split(",")]
    marques = ",".join("?" * len(numeros))
    par_date = {l["date_acquisition"]: l for l in bd().execute(
        "SELECT a.id, a.resume, p.date_acquisition FROM analyse a JOIN produit p ON p.id = a.produit_id "
        f"WHERE a.id IN ({marques})", numeros)}
    date_a = request.args.get("date_a") if request.args.get("date_a") in resume["dates"] else resume["dates"][0]
    date_b = request.args.get("date_b") if request.args.get("date_b") in resume["dates"] else resume["dates"][-1]
    ia, ib = resume["dates"].index(date_a), resume["dates"].index(date_b)
    comparees = []
    for c in indices:
        ha_a, ha_b = serie[ia]["hectares"][c], serie[ib]["hectares"][c]
        comparees.append({"c": c, "nom": resume["classes"][c], "couleur": couleurs[c], "a": ha_a, "b": ha_b,
                          "variation": ha_b - ha_a,
                          "relative": round(100 * (ha_b - ha_a) / ha_a, 1) if ha_a else None})
    cartes_comparees = {"a": par_date.get(date_a), "b": par_date.get(date_b), "date_a": date_a, "date_b": date_b,
                        "classes": comparees,
                        "focus": next((x for x in comparees if x["c"] == classe), None)}

    return render_template(
        "statistiques.html", suivis=[s for s, _ in suivis], choisi=choisi, resume=resume, vue=vue,
        barres=barres, classe=classe, choix_classes=choix_classes, cartes_comparees=cartes_comparees,
        onglet=request.args.get("onglet", "graphiques"),
        serie=serie, indices=indices, couleurs=couleurs, graphe=graphe, tendances=tendances,
        matrice=m, maximum=maximum, classes=CLASSES, couleurs_classes=COULEURS,
        bilan_classes=bilan_classes, ndvi=ndvi, graphe_ndvi=graphe_ndvi, comparaison=comparaison)
