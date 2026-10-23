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
CREATE TABLE IF NOT EXISTS zones (
    suivi_id        INTEGER PRIMARY KEY REFERENCES suivi(id),
    statut          TEXT NOT NULL DEFAULT 'en_attente',
    progression     INTEGER NOT NULL DEFAULT 0,
    message         TEXT NOT NULL DEFAULT '',
    cree_le         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS avis (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    suivi_id        INTEGER NOT NULL REFERENCES suivi(id),
    numero          INTEGER NOT NULL,           -- numéro du foyer
    utilisateur_id  INTEGER NOT NULL REFERENCES utilisateur(id),
    avis            TEXT NOT NULL,              -- visible, non_visible, incertain
    commentaire     TEXT NOT NULL DEFAULT '',
    modifie_le      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (suivi_id, numero)
);
CREATE TABLE IF NOT EXISTS detection (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    utilisateur_id  INTEGER NOT NULL REFERENCES utilisateur(id),
    zone            TEXT NOT NULL,            -- zone demandée : ouest,sud,est,nord
    mois            INTEGER NOT NULL,         -- mois des images (1 à 12)
    annee_debut     INTEGER NOT NULL,
    annee_fin       INTEGER NOT NULL,
    produits        TEXT NOT NULL,            -- produits retenus (un par année)
    analyses        TEXT NOT NULL DEFAULT '', -- analyses utilisées, dans l'ordre des dates
    suivi_id        INTEGER,
    statut          TEXT NOT NULL DEFAULT 'en_attente',
    progression     INTEGER NOT NULL DEFAULT 0,
    message         TEXT NOT NULL DEFAULT '',
    cree_le         TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS suivi (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    utilisateur_id  INTEGER NOT NULL REFERENCES utilisateur(id),
    analyses        TEXT NOT NULL,            -- numéros d'analyses, séparés par des virgules
    tuile           TEXT NOT NULL,
    zone            TEXT NOT NULL,
    statut          TEXT NOT NULL DEFAULT 'en_attente',
    progression     INTEGER NOT NULL DEFAULT 0,
    message         TEXT NOT NULL DEFAULT '',
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
    # Évolution du schéma : emprise géographique du produit
    colonnes = [c["name"] for c in connexion.execute("PRAGMA table_info(produit)")]
    if "emprise" not in colonnes:
        connexion.execute("ALTER TABLE produit ADD COLUMN emprise TEXT NOT NULL DEFAULT ''")
    if "profil" not in [c["name"] for c in connexion.execute("PRAGMA table_info(utilisateur)")]:
        connexion.execute("ALTER TABLE utilisateur ADD COLUMN profil TEXT NOT NULL DEFAULT ''")
    if "sha256" not in colonnes:      # empreinte de l'archive (traçabilité)
        connexion.execute("ALTER TABLE produit ADD COLUMN sha256 TEXT NOT NULL DEFAULT ''")
    connexion.commit()
    connexion.close()

    @app.teardown_appcontext
    def fermer(_exc):
        connexion = g.pop("bd", None)
        if connexion is not None:
            connexion.close()
