# DeepDesert — détection automatique de la désertification par apprentissage profond

DeepDesert est une application web qui détecte des indicateurs de désertification à partir d'images satellitaires **Sentinel-2**. Un réseau **U-Net** classe chaque image en cinq classes. L'application compare ensuite plusieurs années d'une même saison et ne retient comme pertes de végétation que celles qui persistent et que confirme une baisse du **NDVI**.

Projet de fin d'études — Licence en informatique, **ENASTIC** (N'Djaména), année académique 2025–2026.
Autrice : **Maraim Tahir Saleh** · Encadrante : **Mme Widad Aboubakar Hisseine**.

---

## Fonctionnalités

| Module | Rôle |
|---|---|
| Données Sentinel-2 | Import des archives L2A (`.SAFE` ou `.zip`), vérification des bandes et de la couverture nuageuse |
| Prétraitement | Masque des nuages (couche SCL), rééchantillonnage à 10 m, calcul des indices NDVI, NDWI, BSI et NDRE |
| Segmentation U-Net | Classification de chaque pixel en 5 classes : eau, végétation dense, végétation clairsemée, sol nu, sable/dune |
| Cartes et visualisation | Cartes de classes sur fond satellite, NDVI, comparaison de dates |
| Analyse temporelle | Comparaison des années d'une même saison, test de Mann-Kendall et pente de Sen |
| Détection | Pertes de végétation candidates, confirmées par une baisse du NDVI supérieure à 0,10, en taches d'au moins 0,5 ha |
| Zones à surveiller | Localisation des foyers de pertes et vérification sur les images avant / après |
| Rapports et export | Rapport PDF avec traçabilité, GeoTIFF, CSV, GeoJSON et KML |

## Méthode en bref

1. **Entrée** : 10 bandes Sentinel-2 et 4 indices spectraux, soit 14 canaux à 10 m.
2. **Segmentation** : U-Net à trois niveaux (64, 128, 256 filtres ; pont à 512), 7 703 941 paramètres, appliqué par fenêtres de 128 × 128 pixels.
3. **Détection du changement** : pour une même saison, état dominant de chaque pixel sur 2 années sur 3, dans une période de référence et une période récente. Le passage de la végétation à la surface nue est une perte candidate.
4. **Confirmation** : une perte est retenue si le NDVI moyen baisse de plus de 0,10, en taches d'au moins 0,5 ha.

Sur les années de test 2025–2026 de l'étude, le U-Net obtient un Kappa de 0,685 sur cinq classes et un score F1 de 0,939 pour les surfaces nues, contre 0,775 pour une règle classique par seuils de NDVI et de NDWI. Ces scores mesurent l'accord avec des pseudo-masques issus d'ESA WorldCover et de Dynamic World, et non une vérité de terrain. La méthode complète et ses limites sont décrites dans le mémoire.

## Installation (Windows)

Prérequis : **Python 3.12** et **Git**.

```powershell
git clone https://github.com/maraymtaher/detection_desertification.git
cd detection_desertification
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Fichier de configuration

Copiez le modèle de configuration, puis ouvrez `.env` et remplacez la clé secrète par une longue suite de caractères aléatoires :

```powershell
Copy-Item .env.exemple .env
notepad .env
```

### Modèle U-Net

Le fichier du modèle n'est pas stocké dans le dépôt, à cause de sa taille.

1. Téléchargez `unet_meilleur_modele.keras` depuis la page **[Releases](https://github.com/maraymtaher/detection_desertification/releases)** du dépôt.
2. Placez-le dans le dossier `modeles/` :

```
detection_desertification/
└── modeles/
    └── unet_meilleur_modele.keras
```

## Lancement

```powershell
python run.py
```

Ouvrez ensuite **http://deepdesert.localhost:5000**, ou http://127.0.0.1:5000, dans le navigateur. Créez un compte, puis importez au moins deux archives Sentinel-2 L2A d'une même saison et d'années différentes.

Les archives se téléchargent gratuitement sur le [Copernicus Data Space Ecosystem](https://dataspace.copernicus.eu/).

### Options

| Besoin | Commande (PowerShell) |
|---|---|
| Changer de port | `$env:DEEPDESERT_PORT = 8000` puis `python run.py` |
| Ouvrir depuis un téléphone sur le même Wi-Fi | `$env:DEEPDESERT_TELEPHONE = "1"` puis `python run.py` |
| Vérifier la règle de détection | `python -m tests.test_regle_memoire` |
| Réinitialiser un mot de passe | `python reinitialiser_mot_de_passe.py adresse@exemple.com` |

## Organisation du dépôt

```
app/          application web Flask (routes, pages, styles, scripts des cartes)
moteur/       traitement : lecture des archives, U-Net, séries temporelles, zones à surveiller
scripts/      outils en ligne de commande (détection, contrôle en aveugle)
tests/        tests de la règle de détection
modeles/      emplacement du fichier du modèle (non inclus)
config.py     paramètres de l'application
run.py        point d'entrée
```

Les données de l'utilisatrice (base de comptes, archives importées, résultats) sont enregistrées dans `donnees/`. Ce dossier n'est pas publié.

## Données et références principales

- Images : Sentinel-2 niveau 2A, programme Copernicus (ESA).
- Pseudo-masques d'entraînement : ESA WorldCover 10 m et Google Dynamic World.
- Architecture : O. Ronneberger, P. Fischer et T. Brox, « U-Net: Convolutional networks for biomedical image segmentation », *MICCAI*, 2015.
- Zone d'étude : emprise d'environ 70 km sur 77 km autour de Bol (département de Mamdi, province du Lac, Tchad).
