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


def _maj(chemin_base, analyse_id, **champs):
    connexion = connecter(chemin_base)
    colonnes = ", ".join(f"{c} = ?" for c in champs)
    connexion.execute(f"UPDATE analyse SET {colonnes} WHERE id = ?",
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


def _maintenant(chemin_base):
    connexion = connecter(chemin_base)
    valeur = connexion.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
    connexion.close()
    return valeur


def _boucle(chemin_base, dossier_resultats):
    while True:
        analyse_id = _file.get()
        try:
            _executer(chemin_base, dossier_resultats, analyse_id)
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
    en_attente = [l["id"] for l in connexion.execute(
        "SELECT id FROM analyse WHERE statut = 'en_attente' ORDER BY id")]
    connexion.close()
    threading.Thread(
        target=_boucle, args=(chemin_base, app.config["DOSSIER_RESULTATS"]),
        daemon=True, name="executant-analyses").start()
    for analyse_id in en_attente:
        _file.put(analyse_id)


def ajouter(analyse_id):
    _file.put(analyse_id)


def attendre_fin():
    """Bloque jusqu'à ce que la file soit vide (utilisé par les tests)."""
    _file.join()
