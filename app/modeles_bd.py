"""Base de données SQLite : connexion et tables."""
import sqlite3
from pathlib import Path

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS utilisateur (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nom           TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    mot_de_passe  TEXT NOT NULL,              -- haché, jamais en clair
    cree_le       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS produit (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    utilisateur_id      INTEGER NOT NULL REFERENCES utilisateur(id),
    nom                 TEXT NOT NULL,        -- nom du dossier .SAFE
    satellite           TEXT NOT NULL,
    tuile               TEXT NOT NULL,
    date_acquisition    TEXT NOT NULL,        -- AAAA-MM-JJ
    version_traitement  TEXT NOT NULL,
    chemin              TEXT NOT NULL,
    avertissements      TEXT NOT NULL DEFAULT '',
    importe_le          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (utilisateur_id, nom)
);
CREATE TABLE IF NOT EXISTS analyse (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    utilisateur_id  INTEGER NOT NULL REFERENCES utilisateur(id),
    produit_id      INTEGER NOT NULL REFERENCES produit(id),
    zone            TEXT NOT NULL,            -- ouest,sud,est,nord en degrés
    statut          TEXT NOT NULL DEFAULT 'en_attente',
    progression     INTEGER NOT NULL DEFAULT 0,
    message         TEXT NOT NULL DEFAULT '',
    dossier         TEXT NOT NULL DEFAULT '',
    resume          TEXT NOT NULL DEFAULT '', -- contenu de resume.json
    cree_le         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    termine_le      TEXT
);
CREATE TABLE IF NOT EXISTS comparaison (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    utilisateur_id  INTEGER NOT NULL REFERENCES utilisateur(id),
    analyse_a_id    INTEGER NOT NULL REFERENCES analyse(id),
    analyse_b_id    INTEGER NOT NULL REFERENCES analyse(id),
    dossier         TEXT NOT NULL DEFAULT '',
    resume          TEXT NOT NULL DEFAULT '',
    cree_le         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def connecter(chemin):
    """Ouvre une connexion (utilisable aussi hors requête web)."""
    connexion = sqlite3.connect(chemin, timeout=30)
    connexion.row_factory = sqlite3.Row
    connexion.execute("PRAGMA foreign_keys = ON")
    return connexion


def bd():
    """Connexion de la requête en cours."""
    if "bd" not in g:
        g.bd = connecter(current_app.config["CHEMIN_BASE"])
    return g.bd


def initialiser(app):
    Path(app.config["CHEMIN_BASE"]).parent.mkdir(parents=True, exist_ok=True)
    connexion = connecter(app.config["CHEMIN_BASE"])
    connexion.executescript(SCHEMA)
    connexion.commit()
    connexion.close()

    @app.teardown_appcontext
    def fermer(_exc):
        connexion = g.pop("bd", None)
        if connexion is not None:
            connexion.close()
