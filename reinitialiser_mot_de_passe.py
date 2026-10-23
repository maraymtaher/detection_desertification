"""Définit un nouveau mot de passe pour un compte (à lancer par l'administrateur).

    python reinitialiser_mot_de_passe.py adresse@exemple.com
"""
import getpass
import sys

from werkzeug.security import generate_password_hash

import config
from app.modeles_bd import connecter

if len(sys.argv) != 2:
    sys.exit(__doc__)
email = sys.argv[1].strip().lower()
connexion = connecter(str(config.CHEMIN_BASE))
if connexion.execute("SELECT id FROM utilisateur WHERE email = ?", (email,)).fetchone() is None:
    sys.exit(f"Aucun compte avec l'adresse {email}.")
mdp = getpass.getpass("Nouveau mot de passe : ")
if len(mdp) < config.LONGUEUR_MIN_MOT_DE_PASSE:
    sys.exit(f"Au moins {config.LONGUEUR_MIN_MOT_DE_PASSE} caractères.")
if mdp != getpass.getpass("Confirmation : "):
    sys.exit("Les deux mots de passe sont différents.")
connexion.execute("UPDATE utilisateur SET mot_de_passe = ? WHERE email = ?", (generate_password_hash(mdp), email))
connexion.commit()
print("Mot de passe modifié.")
