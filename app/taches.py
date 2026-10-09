"""
Analyses en tâche de fond : une file d'attente et un seul exécutant,
pour ne traiter qu'une analyse à la fois (mémoire limitée de la machine).
"""
import json
import queue
import threading
import traceback
from pathlib import Path

from app.modeles_bd import connecter

_file = queue.Queue()
_demarre = False
_verrou = threading.Lock()

ETAPES = {
    "verification": "Lecture et vérification du produit",
    "segmentation": "Prétraitement et segmentation par le modèle",
    "resultats": "Écriture de la carte et des statistiques",
}


def _maj(chemin_base, analyse_id, table="analyse", **champs):
    connexion = connecter(chemin_base)
    colonnes = ", ".join(f"{c} = ?" for c in champs)
    connexion.execute(f"UPDATE {table} SET {colonnes} WHERE id = ?",
                      (*champs.values(), analyse_id))
    connexion.commit()
    connexion.close()


def _executer(chemin_base, dossier_resultats, analyse_id):
    from moteur import analyse as moteur_analyse     # import tardif (TensorFlow)

    connexion = connecter(chemin_base)
    ligne = connexion.execute(
        "SELECT a.zone, a.statut, p.chemin FROM analyse a "
        "JOIN produit p ON p.id = a.produit_id WHERE a.id = ?",
        (analyse_id,)).fetchone()
    connexion.close()
    if ligne is None or ligne["statut"] != "en_attente":
        return                                        # supprimée ou déjà traitée

    dossier = Path(dossier_resultats) / f"analyse_{analyse_id}"
    _maj(chemin_base, analyse_id, statut="en_cours", progression=0,
         message=ETAPES["verification"], dossier=str(dossier))
    etat = {"etape": "verification", "part": -1}

    def etape(nom):
        etat["etape"] = nom
        part = 96 if nom == "resultats" else max(etat["part"], 0)
        _maj(chemin_base, analyse_id, message=ETAPES[nom], progression=part)

    def progression(faites, total):
        part = 2 + int(93 * faites / max(total, 1))   # de 2 à 95 %
        if part != etat["part"]:
            etat["part"] = part
            _maj(chemin_base, analyse_id, progression=part,
                 message=f"{ETAPES['segmentation']} ({faites}/{total} fenêtres)")

    try:
        zone = tuple(float(v) for v in ligne["zone"].split(","))
        resume = moteur_analyse.analyser(
            ligne["chemin"], dossier, zone_wgs84=zone,
            progression=progression, etape=etape)
        _maj(chemin_base, analyse_id, statut="terminee", progression=100,
             message="Analyse terminée",
             resume=json.dumps(resume, ensure_ascii=False),
             termine_le=_maintenant(chemin_base))
    except moteur_analyse.ErreurAnalyse as e:
        _maj(chemin_base, analyse_id, statut="echouee", message=str(e))
    except Exception as e:                            # erreur imprévue
        traceback.print_exc()
        _maj(chemin_base, analyse_id, statut="echouee",
             message=f"Erreur pendant le traitement : {type(e).__name__} : {e}")


def _executer_suivi(chemin_base, dossier_resultats, suivi_id):
    """Suivi temporel : compare les cartes de plusieurs analyses terminées."""
    from moteur import serie as moteur_serie

    connexion = connecter(chemin_base)
    ligne = connexion.execute(
        "SELECT analyses, statut FROM suivi WHERE id = ?", (suivi_id,)).fetchone()
    if ligne is None or ligne["statut"] != "en_attente":
        connexion.close()
        return
    numeros = [int(v) for v in ligne["analyses"].split(",")]
    marques = ",".join("?" * len(numeros))
    analyses = connexion.execute(
        "SELECT a.id, a.dossier, a.statut, a.resume, p.date_acquisition, p.chemin "
        f"FROM analyse a JOIN produit p ON p.id = a.produit_id WHERE a.id IN ({marques}) "
        "ORDER BY p.date_acquisition", numeros).fetchall()
    connexion.close()

    dossier = Path(dossier_resultats) / f"suivi_{suivi_id}"
    _maj(chemin_base, suivi_id, "suivi", statut="en_cours", progression=0,
         message="Lecture des cartes", dossier=str(dossier))

    def etape(texte, part):
        _maj(chemin_base, suivi_id, "suivi", message=texte, progression=part)

    try:
        if len(analyses) != len(numeros) or any(a["statut"] != "terminee" for a in analyses):
            raise moteur_serie.ErreurSerie(
                "Une des analyses du suivi a été supprimée ou n'est pas terminée.")
        # NDVI de chaque date, calculé une seule fois puis conservé
        # dans le dossier de l'analyse
        from moteur.productivite import calculer_ndvi
        entrees = []
        for i, a in enumerate(analyses):
            classes = Path(a["dossier"]) / "classes.tif"
            ndvi = Path(a["dossier"]) / "ndvi.tif"
            if not ndvi.exists():
                zone = json.loads(a["resume"])["zone_pixels"]
                if not Path(a["chemin"]).exists():
                    raise moteur_serie.ErreurSerie(
                        f"Archive de l'image du {a['date_acquisition']} introuvable : "
                        "impossible de calculer son NDVI.")
                etape(f"Calcul du NDVI du {a['date_acquisition']} ({i + 1}/{len(analyses)})",
                      int(40 * i / len(analyses)))
                provisoire = ndvi.with_suffix(".tmp.tif")
                calculer_ndvi(a["chemin"], zone, provisoire)
                provisoire.replace(ndvi)
            entrees.append((a["date_acquisition"], str(classes), str(ndvi)))

        def etape_serie(texte, part):
            etape(texte, 40 + int(0.6 * part))

        resume = moteur_serie.analyser_serie(entrees, dossier, etape=etape_serie)
        _maj(chemin_base, suivi_id, "suivi", statut="terminee", progression=100,
             message="Suivi terminé", resume=json.dumps(resume, ensure_ascii=False))
    except moteur_serie.ErreurSerie as e:
        _maj(chemin_base, suivi_id, "suivi", statut="echouee", message=str(e))
    except Exception as e:                            # erreur imprévue
        traceback.print_exc()
        _maj(chemin_base, suivi_id, "suivi", statut="echouee",
             message=f"Erreur pendant le calcul : {type(e).__name__} : {e}")


def _maintenant(chemin_base):
    connexion = connecter(chemin_base)
    valeur = connexion.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
    connexion.close()
    return valeur


def _boucle(chemin_base, dossier_resultats):
    while True:
        genre, identifiant = _file.get()
        try:
            if genre == "suivi":
                _executer_suivi(chemin_base, dossier_resultats, identifiant)
            else:
                _executer(chemin_base, dossier_resultats, identifiant)
        finally:
            _file.task_done()


def demarrer(app):
    """Lance l'exécutant et reprend les analyses laissées en suspens."""
    global _demarre
    chemin_base = app.config["CHEMIN_BASE"]
    with _verrou:
        if _demarre:
            return
        _demarre = True
    connexion = connecter(chemin_base)
    connexion.execute(
        "UPDATE analyse SET statut = 'echouee', "
        "message = 'Analyse interrompue par l''arrêt du serveur' "
        "WHERE statut = 'en_cours'")
    connexion.commit()
    connexion.execute(
        "UPDATE suivi SET statut = 'echouee', "
        "message = 'Calcul interrompu par l''arrêt du serveur' "
        "WHERE statut = 'en_cours'")
    connexion.commit()
    en_attente = [("analyse", l["id"]) for l in connexion.execute(
        "SELECT id FROM analyse WHERE statut = 'en_attente' ORDER BY id")]
    en_attente += [("suivi", l["id"]) for l in connexion.execute(
        "SELECT id FROM suivi WHERE statut = 'en_attente' ORDER BY id")]
    connexion.close()
    threading.Thread(
        target=_boucle, args=(chemin_base, app.config["DOSSIER_RESULTATS"]),
        daemon=True, name="executant-analyses").start()
    for tache in en_attente:
        _file.put(tache)


def ajouter(analyse_id):
    _file.put(("analyse", analyse_id))


def ajouter_suivi(suivi_id):
    _file.put(("suivi", suivi_id))


def attendre_fin():
    """Bloque jusqu'à ce que la file soit vide (utilisé par les tests)."""
    _file.join()
