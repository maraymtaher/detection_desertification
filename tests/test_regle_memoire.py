"""
Tests de la règle de détection (méthode version 3 = règle du mémoire).

Lancer depuis le dossier du projet :
    python -m tests.test_regle_memoire
(ou  python -m pytest tests  si pytest est installé)

Chaque test construit une petite série de cartes dont on connaît la bonne
réponse, puis vérifie que le moteur la retrouve.
"""
import numpy as np

from moteur import serie as S

H = L = 40


def _serie(debut, fin, ndvi_debut, ndvi_fin):
    """3 cartes de référence + 3 cartes récentes, NDVI x 10000."""
    classes = np.stack([debut] * 3 + [fin] * 3).astype(np.uint8)
    ndvi = np.stack([ndvi_debut] * 3 + [ndvi_fin] * 3).astype(np.int16)
    return classes, ndvi


def _synthese(classes, ndvi):
    k = 3
    etat_d = S.etat_dominant(classes[:k])
    etat_f = S.etat_dominant(classes[-k:])
    occupation = S.changement_etats(etat_d, etat_f)
    ecart = S.ndvi_moyen_periode(ndvi[-k:], classes[-k:]) - S.ndvi_moyen_periode(ndvi[:k], classes[:k])
    with np.errstate(invalid="ignore"):
        baisse = ecart < -S.SEUIL_BAISSE_NDVI_X10000
    return S.synthese_regle(occupation, baisse, np.isfinite(ecart))


def test_etat_dominant_2_sur_3():
    pile = np.array([[0], [1], [1]], dtype=np.uint8)            # eau, saine, saine
    assert S.etat_dominant(pile)[0] == S.ETAT_VEGETATION
    pile = np.array([[3], [4], [2]], dtype=np.uint8)            # sol nu, sable, clairsemée
    assert S.etat_dominant(pile)[0] == S.ETAT_NU                # sol nu + sable = surface nue
    pile = np.array([[0], [1], [3]], dtype=np.uint8)            # trois états différents
    assert S.etat_dominant(pile)[0] == S.INDETERMINE
    pile = np.array([[1], [255], [255]], dtype=np.uint8)        # nuages 2 fois sur 3
    assert S.etat_dominant(pile)[0] == S.INDETERMINE


def test_perte_confirmee_grande_tache():
    debut = np.full((H, L), 1)                                  # végétation saine
    fin = np.full((H, L), 1)
    fin[5:15, 5:15] = 3                                         # 100 pixels deviennent sol nu
    nd_d = np.full((H, L), 3000)
    nd_f = nd_d.copy()
    nd_f[5:15, 5:15] = 1500                                     # baisse de 0,15
    syn = _synthese(*_serie(debut, fin, nd_d, nd_f))
    assert (syn[5:15, 5:15] == S.CONFIRMEE).all()
    assert (syn[20:, 20:] == S.STABLE).all()


def test_petite_tache_non_confirmee():
    debut = np.full((H, L), 2)                                  # végétation clairsemée
    fin = debut.copy()
    fin[5:10, 5:10] = 4                                         # 25 pixels (0,25 ha) deviennent sable
    nd_d = np.full((H, L), 3000)
    nd_f = nd_d.copy()
    nd_f[5:10, 5:10] = 1000
    syn = _synthese(*_serie(debut, fin, nd_d, nd_f))
    assert (syn[5:10, 5:10] == S.DEGRADATION).all()             # < 0,5 ha : non confirmée


def test_baisse_ndvi_insuffisante():
    debut = np.full((H, L), 1)
    fin = debut.copy()
    fin[5:15, 5:15] = 3
    nd_d = np.full((H, L), 3000)
    nd_f = nd_d.copy()
    nd_f[5:15, 5:15] = 2500                                     # baisse de 0,05 seulement
    syn = _synthese(*_serie(debut, fin, nd_d, nd_f))
    assert (syn[5:15, 5:15] == S.DEGRADATION).all()


def test_gains_et_lac():
    debut = np.full((H, L), 3)                                  # sol nu
    fin = debut.copy()
    fin[0:10, 0:10] = 1                                         # gain de 100 pixels
    fin[30:33, 30:33] = 2                                       # gain de 9 pixels
    debut[20:25, 0:40] = 0                                      # eau au début...
    fin[20:25, 0:40] = 1                                        # ... végétation à la fin
    nd = np.full((H, L), 2000)
    syn = _synthese(*_serie(debut, fin, nd, nd))
    assert (syn[0:10, 0:10] == S.AMELIORATION).all()
    assert (syn[30:33, 30:33] == S.PETIT_GAIN).all()
    assert (syn[20:25, :] == S.HYDRIQUE).all()                  # le lac n'est pas une perte ni un gain


def test_sans_ndvi_pas_de_confirmation():
    debut = np.full((H, L), 1)
    fin = debut.copy()
    fin[5:15, 5:15] = 3
    nd = np.full((H, L), S.SANS_DONNEE_NDVI)                    # nuages partout
    syn = _synthese(*_serie(debut, fin, nd, nd))
    assert (syn[5:15, 5:15] == S.DEGRADATION).all()



def test_controle_qualite():
    dates = ["2017-01-19", "2018-01-24", "2019-02-03", "2020-02-03", "2021-02-27"]
    q = S.controle_qualite(dates, [1, 2, 30, 1, 1], [0.22, 0.23, 0.22, 0.22, 0.10])
    assert q["ecart_jours"] == 39                               # 19 janvier -> 27 février
    assert [x["nuageuse"] for x in q["par_date"]] == [False, False, True, False, False]
    assert [x["atypique"] for x in q["par_date"]] == [False, False, False, False, True]
    assert len(q["avertissements"]) == 3
    assert S.controle_qualite(["2017-12-28", "2018-01-10"], [0, 0])["ecart_jours"] == 13


def test_verite_validation():
    import importlib.util, pathlib
    chemin = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "validation_resultats.py"
    spec = importlib.util.spec_from_file_location("vr", chemin)
    vr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(vr)
    assert vr.verite("vegetation", "surface_nue") == "perte"
    assert vr.verite("surface_nue", "vegetation") == "gain"
    assert vr.verite("vegetation", "eau") == "eau"
    assert vr.verite("illisible", "vegetation") is None
    bas, haut = vr.wilson(16, 17)
    assert 0.70 < bas < 0.75 and 0.98 < haut <= 1.0

if __name__ == "__main__":
    for nom, f in list(globals().items()):
        if nom.startswith("test_"):
            f()
            print("OK :", nom)
    print("Tous les tests de la règle sont réussis.")
