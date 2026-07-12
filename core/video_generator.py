import os
import time
import requests

class HeyGenVideoGenerator:
    def __init__(self, api_key: str = None):
        # Récupère la clé API depuis les variables d'environnement Railway
        self.api_key = api_key or os.getenv("HEYGEN_API_KEY")
        self.base_url = "https://api.heygen.com/v2"
        
        if not self.api_key:
            raise ValueError("❌ HEYGEN_API_KEY manquante dans les variables d'environnement.")

        self.headers = {
            "X-Api-Key": self.api_key,
            "Content-Type": "application/json"
        }

    def generate_lead_video(self, company_name: str, manager_name: str = "Gérant") -> str:
        """
        Lance la génération d'une vidéo avec un avatar standard et retourne l'URL finale.
        """
        endpoint = f"{self.base_url}/video/generate"
        
        # Script personnalisé dicté par l'avatar
        script_text = (
            f"Bonjour, je m'adresse à l'équipe de {company_name}. "
            f"En analysant vos équipements, j'ai remarqué une opportunité majeure "
            f"pour réduire vos factures d'électricité ce mois-ci. "
            f"Regardons ensemble comment Dedall Energy peut vous accompagner."
        )

        # Payload configuré avec un Avatar Standard public ("Benjamin" en costume) 
        # et une voix française de qualité ("fr-FR-HenriNeural")
        payload = {
            "video_inputs": [
                {
                    "character": {
                        "type": "avatar",
                        "avatar_id": "Benjamin_front_professional_public", 
                        "avatar_style": "normal"
                    },
                    "voice": {
                        "type": "text",
                        "input_text": script_text,
                        "voice_id": "0dfbd6516a504c5bb17fa487a329df99" # ID Voix standard FR (Henri)
                    },
                    "background": {
                        "type": "color",
                        "value": "#FAFAFA"
                    }
                }
            ],
            "dimension": {
                "width": 1280,
                "height": 720
            }
        }

        try:
            response = requests.post(endpoint, json=payload, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            
            video_id = data.get("data", {}).get("video_id")
            if not video_id:
                print(f"❌ Réponse HeyGen inattendue : {data}")
                return None
                
            print(f"🎬 Vidéo HeyGen lancée avec succès ! ID: {video_id}")
            
            # On passe à la vérification active du rendu
            return self._wait_for_completion(video_id)

        except Exception as e:
            print(f"❌ Échec de la requête HeyGen : {e}")
            return None

    def _wait_for_completion(self, video_id: str) -> str:
        """
        Boucle de vérification (Polling) pour attendre que la vidéo soit prête.
        """
        # Note : Le statut passe par l'endpoint v1/video_status.get ou v2/video/status
        status_endpoint = f"https://api.heygen.com/v1/video_status.get?video_id={video_id}"
        
        print("⏳ Rendu de la vidéo en cours sur HeyGen (compter ~1 à 2 minutes)...")
        
        while True:
            try:
                res = requests.get(status_endpoint, headers=self.headers).json()
                status_data = res.get("data", {})
                status = status_data.get("status")

                if status == "completed":
                    video_url = status_data.get("video_url")
                    print(f"✅ Vidéo générée avec succès ! URL : {video_url}")
                    return video_url
                
                elif status == "failed":
                    error_msg = status_data.get("error", "Erreur inconnue")
                    print(f"❌ Le rendu HeyGen a échoué : {error_msg}")
                    return None
                
                # Attendre 15 secondes avant la prochaine vérification pour ne pas saturer l'API
                time.sleep(15)
                
            except Exception as e:
                print(f"⚠️ Erreur lors de la vérification du statut : {e}")
                time.sleep(10)
