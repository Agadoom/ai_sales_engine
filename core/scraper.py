import os
import requests
from typing import List, Dict, Any

class LocalBusinessScraper:
    def __init__(self, api_key: str = None):
        # Récupère la clé API Google dans les variables d'environnement
        self.api_key = api_key or os.getenv("GOOGLE_PLACES_API_KEY")
        if not self.api_key:
            raise ValueError("La clé GOOGLE_PLACES_API_KEY est manquante dans ton fichier .env")

        self.base_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        self.details_url = "https://maps.googleapis.com/maps/api/place/details/json"

    def search_businesses(self, query: str) -> List[Dict[str, Any]]:
        """
        Exemple de query : 'Boulangerie Lyon' ou 'Restaurant Marseille'
        """
        # Sécurité : Vérifie que la recherche n'est pas vide
        if not query or not str(query).strip():
            print("⚠️ Recherche vide reçue. Opération annulée.")
            return []

        params = {
            "query": str(query).strip(),
            "key": self.api_key,
            "language": "fr"
        }

        response = requests.get(self.base_url, params=params)
        response.raise_for_status()
        data = response.json()

        # Vérification du statut de l'API Google Places
        status = data.get("status")
        if status != "OK":
            print(f"⚠️ Réponse Google Places API : {status}")
            return []

        results = data.get("results", [])
        formatted_prospects = []

        for place in results[:10]:  # On limite à 10 pour le test
            place_id = place.get("place_id")

            # On va chercher les détails enrichis (site web, téléphone) pour chaque lieu
            details = self._get_place_details(place_id)

            formatted_prospects.append({
                "company_name": place.get("name"),
                "address": place.get("formatted_address"),
                "phone": details.get("formatted_phone_number"),
                "website": details.get("website"),
                "types": place.get("types", []),
                "raw_data": f"Nom: {place.get('name')}. Type Google: {', '.join(place.get('types', []))}. Note: {place.get('rating')}/5 basé sur {place.get('user_ratings_total')} avis."
            })

        return formatted_prospects

    def _get_place_details(self, place_id: str) -> Dict[str, Any]:
        """Récupère le numéro de téléphone et le site web d'un business"""
        if not place_id:
            return {}

        params = {
            "place_id": place_id,
            "fields": "formatted_phone_number,website",
            "key": self.api_key
        }
        response = requests.get(self.details_url, params=params)
        if response.status_code == 200:
            return response.json().get("result", {})
        return {}


# --- TEST DU SCRAPER ---
if __name__ == "__main__":
    try:
        scraper = LocalBusinessScraper()
        print("🔍 Recherche des boulangeries à Bordeaux...")
        prospects = scraper.search_businesses("Boulangerie Bordeaux")

        for p in prospects:
            print(f"\n🏢 {p['company_name']}")
            print(f"🌐 Site : {p['website']}")
            print(f"📊 Données brutes pour l'IA : {p['raw_data']}")

    except Exception as e:
        print(f"❌ Erreur : {e}")
