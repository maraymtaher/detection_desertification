import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rasterio
from moteur.lecture_safe import lire_produit

p = lire_produit(sys.argv[1])
with rasterio.open(p.chemin_gdal(p.bandes["B4"])) as src:
    print("Taille      :", src.width, "x", src.height)
    print("Projection  :", src.crs)
    print("Résolution  :", src.res)
    bloc = src.read(1, window=((5000, 5128), (5000, 5128)))
    print("Bloc lu     :", bloc.shape, "min", bloc.min(), "max", bloc.max())