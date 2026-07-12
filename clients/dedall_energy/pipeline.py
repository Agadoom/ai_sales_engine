# clients/dedall_energy/pipeline.py
import os
import sys
import json
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from core.scraper import LocalBusinessScraper
from core.qualifier import ProspectQualifier
from core.generator import EmailGenerator
from integrations.email_outreach import OutreachConnector

def run_dedall_pipeline(query: str):
    print(f"🚀 Démarrage du pipeline complet Dedall Energy pour : '{query}'")
    
    scraper = LocalBusinessScraper()
    qualifier = ProspectQualifier()
    generator = EmailGenerator()
    outreach = OutreachConnector()
    
    raw_prospects = scraper.search_businesses(query)
    print(f"📋 {len(raw_prospects)} prospects récupérés.")
    
    final_campaign_leads = []
    
    for business in raw_prospects:
        # 1. Qualification
        ai_analysis = qualifier.qualify_prospect(
            company_name=business['company_name'], 
            raw_data=business['raw_data']
        )
        
        # On ne garde que les prospects pertinents (ex: score >= 6)
        if ai_analysis.should_contact and ai_analysis.priority_score >= 6:
            # 2. Génération de l'email
            email = generator.generate_outreach_email(
                company_name=business['company_name'],
                business_type=ai_analysis.business_type,
                hook=ai_analysis.personalized_hook
            )
            
            lead_payload = {
                **business,
                "priority_score": ai_analysis.priority_score,
                "email_subject": email.subject,
                "email_body": email.body
            }
            
            # 3. Push vers la campagne d'envoi
            outreach.send_lead_to_campaign(lead_payload)
            final_campaign_leads.append(lead_payload)

    print(f"\n✨ {len(final_campaign_leads)} prospects qualifiés et prêts à être contactés !")

if __name__ == "__main__":
    run_dedall_pipeline("Restaurant Lyon")
