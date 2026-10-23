"""Point d'entrée : lance le serveur web en local.

    python run.py
puis ouvrir http://deepdesert.localhost:5000 (ou http://127.0.0.1:5000).
"""
from app import creer_app

app = creer_app()

if __name__ == "__main__":
    import os
    nom = "deepdesert.localhost"
    port = int(os.environ.get("DEEPDESERT_PORT", 5000))
    print(f"\n  DeepDesert est lancé : ouvrez http://{nom}:{port}\n")
    hote = "0.0.0.0" if os.environ.get("DEEPDESERT_TELEPHONE") == "1" else "127.0.0.1"
    if hote == "0.0.0.0":
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))          # aucun paquet envoyé : sert à lire l'adresse du PC
            ip = s.getsockname()[0]
            s.close()
        except OSError:
            ip = "adresse-IP-du-PC"
        print(f"  Sur le téléphone (même Wi-Fi) : http://{ip}:{port}\n")
    app.run(host=hote, port=port, debug=False, use_reloader=False)
