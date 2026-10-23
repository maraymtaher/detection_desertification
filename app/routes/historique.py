"""Historique des traitements : toutes les analyses, tous les suivis et tous
les calculs de zones, avec leur état, leur durée et le modèle utilisé.
Permet de relancer une analyse avec exactement la même configuration."""
import json

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from app import taches
from app.modeles_bd import bd
from app.routes.analyses import analyse_ou_404
from app.routes.auth import connexion_requise
from app.routes.main import heure_locale

bp = Blueprint("historique", __name__)

TYPES = {"analyse": "Analyse d'une image", "suivi": "Suivi temporel", "zones": "Zones à surveiller"}
STATUTS = {"terminee": "Terminé", "en_cours": "En cours", "en_attente": "En attente", "echouee": "Échoué"}


def _modele(resume):
    m = (resume.get("tracabilite") or {}).get("modele") if resume else None
    return m["sha256"][:12] if m else None


@bp.route("/historique")
@connexion_requise
def page():
    uid = g.utilisateur["id"]
    lignes = []
    for a in bd().execute(
            "SELECT a.*, p.date_acquisition, p.tuile FROM analyse a JOIN produit p ON p.id = a.produit_id "
            "WHERE a.utilisateur_id = ?", (uid,)):
        r = json.loads(a["resume"]) if a["resume"] else None
        lignes.append({
            "type": "analyse", "id": a["id"], "lance": a["cree_le"],
            "objet": f"Image du {a['date_acquisition']} · tuile {a['tuile']}",
            "detail": f"{r['surface_km2']:,.0f} km²".replace(",", " ") if r else "",
            "statut": a["statut"], "message": a["message"] if a["statut"] == "echouee" else "",
            "duree": r["duree_secondes"] if r else None, "modele": _modele(r),
            "lien": url_for("analyses.detail", analyse_id=a["id"])})
    for s in bd().execute("SELECT * FROM suivi WHERE utilisateur_id = ?", (uid,)):
        r = json.loads(s["resume"]) if s["resume"] else None
        lignes.append({
            "type": "suivi", "id": s["id"], "lance": s["cree_le"],
            "objet": (f"{r['nb_dates']} images · {r['dates'][0]} → {r['dates'][-1]}" if r
                      else f"Analyses n° {s['analyses'].replace(',', ', ')}"),
            "detail": f"méthode v{r.get('version')}" if r else "",
            "statut": s["statut"], "message": s["message"] if s["statut"] == "echouee" else "",
            "duree": None, "modele": None, "lien": url_for("suivi.detail", suivi_id=s["id"])})
    for z in bd().execute("SELECT z.* FROM zones z JOIN suivi s ON s.id = z.suivi_id "
                          "WHERE s.utilisateur_id = ?", (uid,)):
        lignes.append({
            "type": "zones", "id": z["suivi_id"], "lance": z["cree_le"],
            "objet": f"Foyers de dégradation du suivi n° {z['suivi_id']}", "detail": "",
            "statut": z["statut"], "message": z["message"] if z["statut"] == "echouee" else "",
            "duree": None, "modele": None, "lien": url_for("zones.detail", suivi_id=z["suivi_id"])})
    lignes.sort(key=lambda l: l["lance"] or "", reverse=True)

    filtres = {k: request.args.get(k, "") for k in ("type", "statut")}
    comptes = {k: sum(1 for l in lignes if l["statut"] == k) for k in STATUTS}
    visibles = [l for l in lignes if (not filtres["type"] or l["type"] == filtres["type"])
                and (not filtres["statut"] or l["statut"] == filtres["statut"])]
    for l in visibles:
        l["lance_local"] = heure_locale(l["lance"])
    return render_template("historique.html", lignes=visibles, total=len(lignes), comptes=comptes,
                           types=TYPES, statuts=STATUTS, filtres=filtres)


@bp.route("/historique/analyse/<int:analyse_id>/relancer", methods=["POST"])
@connexion_requise
def relancer_analyse(analyse_id):
    """Nouvelle analyse avec la même image et exactement la même zone.
    L'ancienne est conservée, pour pouvoir comparer les deux résultats."""
    ancienne = analyse_ou_404(analyse_id)
    curseur = bd().execute(
        "INSERT INTO analyse (utilisateur_id, produit_id, zone) VALUES (?, ?, ?)",
        (g.utilisateur["id"], ancienne["produit_id"], ancienne["zone"]))
    bd().commit()
    taches.ajouter(curseur.lastrowid)
    flash(f"Analyse n° {analyse_id} relancée avec la même configuration : nouvelle analyse "
          f"n° {curseur.lastrowid}.", "success")
    return redirect(url_for("analyses.detail", analyse_id=curseur.lastrowid))
