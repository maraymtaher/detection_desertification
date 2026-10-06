"""Point d'entrée : lance le serveur web en local.

    python run.py
puis ouvrir http://127.0.0.1:5000 dans le navigateur.
"""
from app import creer_app

app = creer_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
