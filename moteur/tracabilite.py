"""
Traçabilité : identifier sans ambiguïté ce qui a produit un résultat.

  - VERSION_SYSTEME : version du code de la plateforme ;
  - empreinte SHA-256 du fichier du modèle : deux résultats obtenus avec la
    même empreinte viennent exactement du même modèle ;
  - empreinte SHA-256 d'un fichier (archive Sentinel-2, résultat) : permet
    de vérifier plus tard qu'un fichier n'a pas été modifié.
"""
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from moteur.config_modele import CHEMIN_MODELE

VERSION_SYSTEME = "1.0"
TAILLE_BLOC = 8 * 1024 * 1024

_cache = {}


def sha256_fichier(chemin):
    """Empreinte SHA-256 d'un fichier, lue par blocs (fichiers de plusieurs Go).
    Gardée en mémoire tant que le fichier n'a pas changé (taille et date)."""
    chemin = Path(chemin)
    info = chemin.stat()
    cle = (str(chemin.resolve()), info.st_size, info.st_mtime_ns)
    if cle not in _cache:
        h = hashlib.sha256()
        with open(chemin, "rb") as f:
            for bloc in iter(lambda: f.read(TAILLE_BLOC), b""):
                h.update(bloc)
        _cache[cle] = h.hexdigest()
    return _cache[cle]


def _horodatage(secondes):
    return datetime.fromtimestamp(secondes, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def empreinte_modele(chemin=CHEMIN_MODELE):
    """Identité du fichier du modèle. Renvoie None si le fichier est absent."""
    chemin = Path(chemin)
    if not chemin.is_file():
        return None
    info = chemin.stat()
    return {
        "fichier": chemin.name,
        "sha256": sha256_fichier(chemin),
        "taille_octets": info.st_size,
        "modifie_le": _horodatage(info.st_mtime),
    }


def maintenant():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def trace_analyse(produit, zone_pixels):
    """Bloc « traçabilité » enregistré dans le résumé de chaque analyse."""
    return {
        "version_systeme": VERSION_SYSTEME,
        "calcule_le": maintenant(),
        "modele": empreinte_modele(),
        "produit": produit.nom,
        "version_traitement_esa": produit.version_traitement,
        "decalages_radiometriques": dict(produit.decalages),
        "zone_pixels": [int(v) for v in zone_pixels],
    }
