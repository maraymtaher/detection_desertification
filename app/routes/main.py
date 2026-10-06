"""Accueil et tableau de bord."""
from flask import Blueprint, g, render_template

from app.modeles_bd import bd

bp = Blueprint("main", __name__)


@bp.route("/")
def accueil():
    if g.utilisateur is None:
        return render_template("accueil.html")
    uid = g.utilisateur["id"]
    produits = bd().execute(
        "SELECT * FROM produit WHERE utilisateur_id = ? "
        "ORDER BY date_acquisition DESC, id DESC", (uid,)).fetchall()
    compte = lambda statut: bd().execute(
        "SELECT COUNT(*) FROM analyse WHERE utilisateur_id = ? AND statut = ?",
        (uid, statut)).fetchone()[0]
    chiffres = {
        "produits": len(produits),
        "analyses": compte("terminee"),
        "en_cours": compte("en_cours") + compte("en_attente"),
        "derniere": produits[0]["date_acquisition"] if produits else "—",
    }
    return render_template("tableau_de_bord.html", produits=produits,
                           chiffres=chiffres)
