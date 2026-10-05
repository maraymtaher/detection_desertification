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

# Classes de sortie
CLASSES = ["Eau", "Végétation saine", "Végétation dégradée", "Sol nu", "Sable/Dune"]
COULEURS = ["#0000FF", "#006400", "#90EE90", "#D2B48C", "#FFA500"]
NON_CLASSE = 255

# Valeurs de la couche SCL considérées comme invalides :
# 0 sans donnée, 1 saturé/défectueux, 3 ombre de nuage,
# 8 et 9 nuages, 10 cirrus
SCL_INVALIDES = [0, 1, 3, 8, 9, 10]

# Tuiles sur lesquelles le modèle a été entraîné (zone de Bol)
TUILES_ENTRAINEMENT = {"33PVQ", "33PVR"}