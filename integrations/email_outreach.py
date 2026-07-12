# integrations/email_outreach.py
import os
import requests
from typing import Dict, Any

class OutreachConnector:
    def __init__(self, webhook_url: str = None):
        # L'URL de ton Webhook Smartlead / Lemlist / Make / n8n
        self.webhook_url = webhook_url or os.getenv("OUTREACH_WEBHOOK_URL")

    def send_lead_to_campaign(self, lead_data: Dict[str, Any]) -> bool:
        if not self.webhook_url:
            print("⚠️ OUTREACH_WEBHOOK_URL non configuré. Envoi simulé.")
            # En mode test/simulation, on renvoie True
            return True

        try:
            response = requests.post(self.webhook_url, json=lead_data, timeout=10)
            if response.status_code in [200, 201]:
                print(f"✅ Lead envoyé à la campagne : {lead_data.get('company_name')}")
                return True
            else:
                print(f"❌ Échec d'envoi webhook : {response.status_code} - {response.text}")
                return False
        except Exception as e:
            print(f"❌ Erreur connexion Webhook : {e}")
            return False
