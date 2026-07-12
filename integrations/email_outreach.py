# integrations/email_outreach.py
import os
import requests
from typing import Dict, Any

class OutreachConnector:
    def __init__(self, webhook_url: str = None):
        # Tu peux mettre l'URL de ton webhook Lemlist/Smartlead/Make dans tes variables Railway
        self.webhook_url = webhook_url or os.getenv("OUTREACH_WEBHOOK_URL")

    def send_lead_to_campaign(self, lead_data: Dict[str, Any]) -> bool:
        if not self.webhook_url:
            print("⚠️ Aucun Webhook configuré, envoi simulé.")
            return True

        response = requests.post(self.webhook_url, json=lead_data)
        if response.status_code in [200, 201]:
            print(f"✅ Lead envoyé avec succès : {lead_data.get('company_name')}")
            return True
        else:
            print(f"❌ Échec de l'envoi : {response.status_code} - {response.text}")
            return False
