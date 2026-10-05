"""
M1 — Lecture et vérification d'une archive Sentinel-2 L2A (.zip SAFE).

Ce module n'ouvre aucune image : il lit la liste des fichiers et les
métadonnées, sans décompresser l'archive.
"""
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from moteur.config_modele import (
    BANDES_MODELE, QUANTIFICATION, TUILES_ENTRAINEMENT)

# Numéro de bande utilisé dans les métadonnées pour BOA_ADD_OFFSET
ID_BANDE_XML = {"B01": 0, "B02": 1, "B03": 2, "B04": 3, "B05": 4, "B06": 5,
                "B07": 6, "B08": 7, "B8A": 8, "B09": 9, "B10": 10,
                "B11": 11, "B12": 12}

NOM_PRODUIT = re.compile(
    r"(S2[A-D])_MSI(L\d[A-C])_(\d{8})T(\d{6})_N(\d{4})_R(\d{3})_T(\w{5})_")


@dataclass
class ProduitSafe:
    chemin_zip: str
    nom: str = ""
    satellite: str = ""
    niveau: str = ""
    date: str = ""                 # AAAA-MM-JJ
    tuile: str = ""
    version_traitement: str = ""   # ex. "05.11"
    quantification: float = QUANTIFICATION
    decalages: dict = field(default_factory=dict)  # nom notebook -> décalage
    bandes: dict = field(default_factory=dict)     # nom notebook -> chemin dans le zip
    scl: str = ""                  # chemin de la couche SCL (20 m)
    erreurs: list = field(default_factory=list)
    avertissements: list = field(default_factory=list)

    @property
    def valide(self):
        return not self.erreurs

    def chemin_gdal(self, chemin_interne):
        """Chemin lisible par Rasterio sans décompresser l'archive."""
        return f"/vsizip/{self.chemin_zip}/{chemin_interne}"


def _balise(element):
    """Nom de balise sans l'espace de noms XML."""
    return element.tag.split("}")[-1]


def lire_produit(chemin_zip):
    """Lit une archive SAFE et renvoie un ProduitSafe (valide ou non)."""
    p = ProduitSafe(chemin_zip=str(chemin_zip))

    if not zipfile.is_zipfile(chemin_zip):
        p.erreurs.append("Le fichier n'est pas une archive .zip lisible.")
        return p

    with zipfile.ZipFile(chemin_zip) as z:
        fichiers = z.namelist()

        # --- 1. Nom du produit ---
        dossiers = {f.split("/")[0] for f in fichiers
                    if f.split("/")[0].endswith(".SAFE")}
        if len(dossiers) != 1:
            p.erreurs.append("L'archive doit contenir exactement un dossier .SAFE.")
            return p
        p.nom = dossiers.pop()
        m = NOM_PRODUIT.match(p.nom)
        if not m:
            p.erreurs.append(f"Nom de produit non reconnu : {p.nom}")
            return p
        p.satellite, p.niveau, jour, _, version, _, p.tuile = m.groups()
        p.date = f"{jour[:4]}-{jour[4:6]}-{jour[6:]}"
        p.version_traitement = f"{version[:2]}.{version[2:]}"

        if p.niveau != "L2A":
            p.erreurs.append(
                f"Niveau {p.niveau} : le modèle exige un produit de niveau L2A.")
        if p.tuile not in TUILES_ENTRAINEMENT:
            p.avertissements.append(
                f"Tuile {p.tuile} hors de la zone d'entraînement "
                f"({', '.join(sorted(TUILES_ENTRAINEMENT))}) : résultat non garanti.")

        # --- 2. Localisation des bandes et de la couche SCL ---
        for nom_modele, nom_safe, res in BANDES_MODELE:
            motif = re.compile(
                rf"/IMG_DATA/R{res}m/[^/]*_{nom_safe}_{res}m\.jp2$")
            trouves = [f for f in fichiers if motif.search(f)]
            if len(trouves) == 1:
                p.bandes[nom_modele] = trouves[0]
            else:
                p.erreurs.append(
                    f"Bande {nom_safe} à {res} m : {len(trouves)} fichier(s) "
                    f"trouvé(s), 1 attendu.")
        scl = [f for f in fichiers
               if re.search(r"/IMG_DATA/R20m/[^/]*_SCL_20m\.jp2$", f)]
        if len(scl) == 1:
            p.scl = scl[0]
        else:
            p.erreurs.append("Couche SCL (masque nuages) à 20 m introuvable.")

        # --- 3. Métadonnées : quantification et décalage radiométrique ---
        chemin_meta = f"{p.nom}/MTD_MSIL2A.xml"
        if chemin_meta not in fichiers:
            p.erreurs.append("Fichier de métadonnées MTD_MSIL2A.xml introuvable.")
            return p
        racine = ET.fromstring(z.read(chemin_meta))
        decalages_xml = {}
        for e in racine.iter():
            nom = _balise(e)
            if nom == "BOA_QUANTIFICATION_VALUE" and e.text:
                p.quantification = float(e.text)
            elif nom == "BOA_ADD_OFFSET" and e.text:
                decalages_xml[int(e.get("band_id"))] = float(e.text)

        for nom_modele, nom_safe, _ in BANDES_MODELE:
            p.decalages[nom_modele] = decalages_xml.get(ID_BANDE_XML[nom_safe], 0.0)

        if not decalages_xml and p.version_traitement >= "04.00":
            p.avertissements.append(
                "Version de traitement >= 04.00 mais aucun décalage "
                "BOA_ADD_OFFSET lu dans les métadonnées : à vérifier.")
        if p.quantification != QUANTIFICATION:
            p.avertissements.append(
                f"Quantification = {p.quantification} "
                f"({QUANTIFICATION:.0f} attendu).")
    return p