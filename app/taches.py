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
from app.indicateurs import VERSION_METHODE

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
        from moteur import tracabilite
        resume["tracabilite"] = {
            "version_systeme": tracabilite.VERSION_SYSTEME,
            "calcule_le": tracabilite.maintenant(),
            "analyses": [a["id"] for a in analyses],
        }
        _maj(chemin_base, suivi_id, "suivi", statut="terminee", progression=100,
             message="Suivi terminé", resume=json.dumps(resume, ensure_ascii=False))
    except moteur_serie.ErreurSerie as e:
        _maj(chemin_base, suivi_id, "suivi", statut="echouee", message=str(e))
    except Exception as e:                            # erreur imprévue
        traceback.print_exc()
        _maj(chemin_base, suivi_id, "suivi", statut="echouee",
             message=f"Erreur pendant le calcul : {type(e).__name__} : {e}")


def entrees_suivi(chemin_base, suivi_id):
    """Analyses d'un suivi, triées par date, avec leurs fichiers."""
    connexion = connecter(chemin_base)
    ligne = connexion.execute("SELECT analyses FROM suivi WHERE id = ?", (suivi_id,)).fetchone()
    numeros = [int(v) for v in ligne["analyses"].split(",")]
    marques = ",".join("?" * len(numeros))
    analyses = connexion.execute(
        "SELECT a.dossier, a.resume, p.date_acquisition, p.chemin FROM analyse a "
        f"JOIN produit p ON p.id = a.produit_id WHERE a.id IN ({marques}) "
        "ORDER BY p.date_acquisition", numeros).fetchall()
    connexion.close()
    return [{"date": a["date_acquisition"],
             "classes": str(Path(a["dossier"]) / "classes.tif"),
             "ndvi": str(Path(a["dossier"]) / "ndvi.tif"),
             "archive": a["chemin"],
             "zone_pixels": json.loads(a["resume"])["zone_pixels"]} for a in analyses]


def _executer_foyers(chemin_base, suivi_id):
    """Zones à surveiller d'un suivi terminé."""
    from moteur import foyers as moteur_foyers

    connexion = connecter(chemin_base)
    suivi = connexion.execute("SELECT dossier, statut, resume FROM suivi WHERE id = ?",
                              (suivi_id,)).fetchone()
    zones = connexion.execute("SELECT statut FROM zones WHERE suivi_id = ?",
                              (suivi_id,)).fetchone()
    connexion.close()
    if suivi is None or zones is None or zones["statut"] != "en_attente":
        return

    def maj(**champs):
        c = connecter(chemin_base)
        colonnes = ", ".join(f"{k} = ?" for k in champs)
        c.execute(f"UPDATE zones SET {colonnes} WHERE suivi_id = ?", (*champs.values(), suivi_id))
        c.commit()
        c.close()

    maj(statut="en_cours", progression=0, message="Démarrage")
    try:
        version = json.loads(suivi["resume"]).get("version") if suivi["resume"] else None
        if suivi["statut"] != "terminee" or version != VERSION_METHODE:
            raise ValueError("Le suivi doit être terminé et calculé avec la méthode actuelle "
                             "(cliquez sur « Recalculer » dans la page du suivi).")
        moteur_foyers.detecter_foyers(
            suivi["dossier"], entrees_suivi(chemin_base, suivi_id),
            etape=lambda texte, part: maj(message=texte, progression=part))
        maj(statut="terminee", progression=100, message="Zones identifiées")
    except Exception as e:
        traceback.print_exc()
        maj(statut="echouee", message=f"{type(e).__name__} : {e}")


def _executer_detection(chemin_base, dossier_resultats, detection_id):
    """Chaîne complète de détection, sans intervention :
    1. analyse U-Net de chaque image retenue (les analyses déjà faites
       sur la même zone sont réutilisées) ;
    2. suivi temporel (règle du mémoire : changement d'état + contrôle NDVI,
       tendances de Mann-Kendall) ;
    3. zones à surveiller (foyers de dégradation et images avant/après)."""
    connexion = connecter(chemin_base)
    d = connexion.execute("SELECT * FROM detection WHERE id = ?", (detection_id,)).fetchone()
    connexion.close()
    if d is None or d["statut"] != "en_attente":
        return

    def maj(**champs):
        _maj(chemin_base, detection_id, "detection", **champs)

    def lire(requete, valeurs=()):
        c = connecter(chemin_base)
        ligne = c.execute(requete, valeurs).fetchone()
        c.close()
        return ligne

    def ecrire(requete, valeurs=()):
        c = connecter(chemin_base)
        curseur = c.execute(requete, valeurs)
        c.commit()
        c.close()
        return curseur.lastrowid

    maj(statut="en_cours", progression=1, message="Préparation des images")
    try:
        produits = [int(v) for v in d["produits"].split(",")]
        n = len(produits)
        analyses = []
        # --- 1. Une analyse par image ---
        for i, pid in enumerate(produits):
            p = lire("SELECT date_acquisition FROM produit WHERE id = ?", (pid,))
            existante = lire("SELECT id FROM analyse WHERE produit_id = ? AND zone = ? AND statut = 'terminee' "
                             "ORDER BY id DESC LIMIT 1", (pid, d["zone"]))
            if existante:
                analyses.append(existante["id"])
                maj(progression=2 + int(68 * (i + 1) / n),
                    message=f"Image du {p['date_acquisition']} : analyse déjà disponible ({i + 1}/{n})")
                continue
            maj(progression=2 + int(68 * i / n),
                message=f"Segmentation U-Net de l'image du {p['date_acquisition']} ({i + 1}/{n})")
            aid = ecrire("INSERT INTO analyse (utilisateur_id, produit_id, zone) VALUES (?, ?, ?)",
                         (d["utilisateur_id"], pid, d["zone"]))
            maj(analyses=",".join(str(a) for a in analyses + [aid]))
            _executer(chemin_base, dossier_resultats, aid)
            fin = lire("SELECT statut, message FROM analyse WHERE id = ?", (aid,))
            if fin["statut"] != "terminee":
                raise RuntimeError(f"Analyse de l'image du {p['date_acquisition']} impossible : {fin['message']}")
            analyses.append(aid)
        maj(analyses=",".join(str(a) for a in analyses))

        # --- 2. Suivi temporel ---
        liste = ",".join(str(a) for a in sorted(analyses))
        suivi = lire("SELECT id, resume FROM suivi WHERE utilisateur_id = ? AND analyses = ? "
                     "AND statut = 'terminee' ORDER BY id DESC LIMIT 1", (d["utilisateur_id"], liste))
        if suivi and json.loads(suivi["resume"]).get("version") == VERSION_METHODE:
            suivi_id = suivi["id"]
        else:
            tuile = lire("SELECT tuile FROM produit WHERE id = ?", (produits[0],))["tuile"]
            suivi_id = ecrire("INSERT INTO suivi (utilisateur_id, analyses, tuile, zone) VALUES (?, ?, ?, ?)",
                              (d["utilisateur_id"], liste, tuile, d["zone"]))
            maj(suivi_id=suivi_id, progression=72,
                message="Suivi temporel : changements d'état, contrôle NDVI et tendances")
            _executer_suivi(chemin_base, dossier_resultats, suivi_id)
            fin = lire("SELECT statut, message FROM suivi WHERE id = ?", (suivi_id,))
            if fin["statut"] != "terminee":
                raise RuntimeError(f"Suivi temporel impossible : {fin['message']}")
        maj(suivi_id=suivi_id, progression=90, message="Recherche des foyers de perte de végétation")

        # --- 3. Zones à surveiller ---
        zones = lire("SELECT statut FROM zones WHERE suivi_id = ?", (suivi_id,))
        if not zones or zones["statut"] != "terminee":
            ecrire("DELETE FROM avis WHERE suivi_id = ?", (suivi_id,))
            ecrire("DELETE FROM zones WHERE suivi_id = ?", (suivi_id,))
            ecrire("INSERT INTO zones (suivi_id) VALUES (?)", (suivi_id,))
            _executer_foyers(chemin_base, suivi_id)
            fin = lire("SELECT statut, message FROM zones WHERE suivi_id = ?", (suivi_id,))
            if fin["statut"] != "terminee":
                raise RuntimeError(f"Recherche des zones impossible : {fin['message']}")
        maj(statut="terminee", progression=100, message="Détection terminée")
    except Exception as e:
        traceback.print_exc()
        maj(statut="echouee", message=str(e))


def _maintenant(chemin_base):
    connexion = connecter(chemin_base)
    valeur = connexion.execute("SELECT CURRENT_TIMESTAMP").fetchone()[0]
    connexion.close()
    return valeur


def _boucle(chemin_base, dossier_resultats):
    while True:
        genre, identifiant = _file.get()
        try:
            if genre == "detection":
                _executer_detection(chemin_base, dossier_resultats, identifiant)
            elif genre == "suivi":
                _executer_suivi(chemin_base, dossier_resultats, identifiant)
            elif genre == "foyers":
                _executer_foyers(chemin_base, identifiant)
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
    connexion.execute("UPDATE zones SET statut = 'echouee', message = 'Calcul interrompu "
                      "par l''arrêt du serveur' WHERE statut = 'en_cours'")
    connexion.commit()
    en_attente += [("foyers", l["suivi_id"]) for l in connexion.execute(
        "SELECT suivi_id FROM zones WHERE statut = 'en_attente'")]
    connexion.execute("UPDATE detection SET statut = 'echouee', message = 'Détection interrompue "
                      "par l''arrêt du serveur : relancez-la' WHERE statut = 'en_cours'")
    connexion.commit()
    en_attente += [("detection", l["id"]) for l in connexion.execute(
        "SELECT id FROM detection WHERE statut = 'en_attente' ORDER BY id")]
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


def ajouter_detection(detection_id):
    _file.put(("detection", detection_id))


def ajouter_foyers(suivi_id):
    _file.put(("foyers", suivi_id))


def attendre_fin():
    """Bloque jusqu'à ce que la file soit vide (utilisé par les tests)."""
    _file.join()
