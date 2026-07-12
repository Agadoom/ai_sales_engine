# main.py (à la racine du projet)
from clients.dedall_energy.pipeline import run_dedall_pipeline

if __name__ == "__main__":
    print("🚀 Démarrage du moteur IA via main.py sur Railway...")
    
    # Remplace par la recherche par défaut que tu veux exécuter au lancement
    run_dedall_pipeline("Boulangerie Paris")
