import os
import requests
import time

HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY")

def generate_personalized_video(company_name: str, contact_name: str = "Gérant") -> str:
    """
    Génère une vidéo personnalisée via HeyGen et retourne l'URL de la vidéo.
    """
    url = "https://api.heygen.com/v2/video/generate"
    
    headers = {
        "X-Api-Key": HEYGEN_API_KEY,
        "Content-Type": "application/json"
    }

    # Script personnalisé prononcé par l'avatar IA
    script_text = (
        f"Bonjour {contact_name}, j'ai analysé l'empreinte énergétique de {company_name}. "
        f"En tant que boulangerie, vos équipements tournent en continu. "
        f"Laissez-moi vous montrer comment Dedall Energy peut réduire votre facture d'électricité dès ce mois-ci."
    )

    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": "TON_AVATAR_ID_ICI",  # Ex: "Ann_Doctor_Standing2_public"
                    "avatar_style": "normal"
                },
                "voice": {
                    "type": "text",
                    "input_text": script_text,
                    "voice_id": "TA_VOIX_ID_ICI"       # Ex: FR_French_Male_1
                },
                "background": {
                    "type": "color",
                    "value": "#FFFFFF"
                }
            }
        ],
        "dimension": {
            "width": 1280,
            "height": 720
        }
    }

    response = requests.post(url, json=payload, headers=headers)
    data = response.json()

    if response.status_code == 200 and "data" in data:
        video_id = data["data"]["video_id"]
        print(f"🎬 Génération vidéo lancée (ID: {video_id})...")
        
        # Attente du rendu vidéo (HeyGen prend généralement 1 à 2 min)
        return wait_for_video_url(video_id, headers)
    else:
        print(f"❌ Erreur HeyGen : {data}")
        return None

def wait_for_video_url(video_id: str, headers: dict) -> str:
    """
    Vérifie le statut de rendu de la vidéo jusqu'à ce qu'elle soit prête.
    """
    status_url = f"https://api.heygen.com/v1/video_status.get?video_id={video_id}"
    
    while True:
        res = requests.get(status_url, headers=headers).json()
        status = res.get("data", {}).get("status")

        if status == "completed":
            video_url = res["data"]["video_url"]
            print(f"✅ Vidéo prête : {video_url}")
            return video_url
        elif status == "failed":
            print("❌ Échec du rendu vidéo.")
            return None
        
        print("⏳ Rendu en cours...")
        time.sleep(10)  # Vérifie toutes les 10 secondes
