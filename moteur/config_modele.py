"""
Configuration centrale du modèle U-Net.
Toute constante liée au modèle est définie ICI et nulle part ailleurs.
"""

# Fichier du modèle (relatif à la racine du projet)
CHEMIN_MODELE = "modeles/unet_meilleur_modele.keras"

# Bandes attendues, dans l'ORDRE EXACT de l'entraînement :
# (nom dans le notebook, nom dans le produit SAFE, résolution native en m)
BANDES_MODELE = [
    ("B2", "B02", 10), ("B3", "B03", 10), ("B4", "B04", 10), ("B8", "B08", 10),
    ("B5", "B05", 20), ("B6", "B06", 20), ("B7", "B07", 20), ("B8A", "B8A", 20),
    ("B11", "B11", 20), ("B12", "B12", 20),
]
# Indices ajoutés après les bandes, dans cet ordre (canaux 11 à 14)
INDICES_MODELE = ["NDVI", "NDWI", "BSI", "NDRE"]
NB_CANAUX = len(BANDES_MODELE) + len(INDICES_MODELE)   # 14

TAILLE_FENETRE = 128      # pixels
RESOLUTION = 10           # mètres
QUANTIFICATION = 10000.0  # réflectance = valeur / 10000

# Classes de sortie. La classe 2 regroupe les couverts épars (NDVI
# typiquement entre 0,15 et 0,40), d'où son libellé.
CLASSES = ["Eau", "Végétation saine", "Végétation clairsemée ou dégradée",
           "Sol nu", "Sable/Dune"]
COULEURS = ["#0000FF", "#006400", "#90EE90", "#D2B48C", "#FFA500"]
NON_CLASSE = 255

# Couche SCL : mêmes classes masquées que dans le notebook d'entraînement
# (3 ombre de nuage, 8 et 9 nuages, 10 cirrus), plus 0 = sans donnée
SCL_NUAGES = [3, 8, 9, 10]
SCL_SANS_DONNEE = 0

# ---------------------------------------------------------------------
# Domaine de validité du modèle
# ---------------------------------------------------------------------
# Tuiles Sentinel-2 qui recouvrent la zone d'entraînement
TUILES_ENTRAINEMENT = {"33PVQ", "33PVR"}
# Rectangle d'entraînement (notebook) : ouest, sud, est, nord, en degrés
EMPRISE_ENTRAINEMENT = (14.40, 13.10, 15.05, 13.80)

# Saisons autour du lac Tchad. Environ 90 % des pluies tombent de juin
# à septembre ; le lac monte ensuite de novembre à février (USGS).
# Deux dates ne sont comparables que si elles sont de la même saison.
SAISONS = {
    "pluies": (6, 7, 8, 9),
    "seche_fraiche": (10, 11, 12, 1, 2),
    "seche_chaude": (3, 4, 5),
}
LIBELLES_SAISONS = {
    "pluies": "Saison des pluies (juin à septembre)",
    "seche_fraiche": "Saison sèche fraîche (octobre à février)",
    "seche_chaude": "Saison sèche chaude (mars à mai)",
}


def saison(date_iso):
    """Saison d'une date au format AAAA-MM-JJ."""
    mois = int(date_iso[5:7])
    for nom, liste_mois in SAISONS.items():
        if mois in liste_mois:
            return nom
    raise ValueError(f"Date invalide : {date_iso}")