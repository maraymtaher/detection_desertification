"""
Paramètres de l'application web (chemins, clé secrète, limites techniques).
Les constantes scientifiques du modèle sont dans moteur/config_modele.py.
"""
import os
import secrets
from pathlib import Path

RACINE = Path(__file__).resolve().parent
DOSSIER_DONNEES = RACINE / "donnees"
DOSSIER_IMPORTS = DOSSIER_DONNEES / "imports"
DOSSIER_RESULTATS = DOSSIER_DONNEES / "resultats"
CHEMIN_BASE = DOSSIER_DONNEES / "base.sqlite"

# Limite technique d'envoi : une tuile L2A pèse environ 1 Go
TAILLE_MAX_ENVOI = 1536 * 1024 * 1024      # 1,5 Go
LONGUEUR_MIN_MOT_DE_PASSE = 8


def _cle_secrete():
    """Lit la clé dans .env ; la crée au premier lancement."""
    fichier = RACINE / ".env"
    if fichier.exists():
        for ligne in fichier.read_text(encoding="utf-8").splitlines():
            if ligne.startswith("CLE_SECRETE=") and len(ligne) > 30:
                return ligne.split("=", 1)[1].strip()
    cle = secrets.token_hex(32)
    fichier.write_text(f"CLE_SECRETE={cle}\n", encoding="utf-8")
    return cle


CLE_SECRETE = os.environ.get("CLE_SECRETE") or _cle_secrete()
