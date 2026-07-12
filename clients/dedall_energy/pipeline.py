# clients/dedall_energy/pipeline.py
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from core.scraper import LocalBusinessScraper
from core.qualifier import ProspectQualifier
from core.generator import EmailGenerator
from core.database import SessionLocal, LeadModel, init_db

def run_dedall_pipeline(query: str):
    print(f"🚀 Démarrage du pipeline avec BDD PostgreSQL pour : '{query}'")
    
    # Initialisation de la BDD
    init_db()
    db = SessionLocal()
    
    scraper = LocalBusinessScraper()
    qualifier = ProspectQualifier()
    generator = EmailGenerator()
    
    raw_prospects = scraper.search_businesses(query)
    print(f"📋 {len(raw_prospects)} prospects trouvés.")
    
    saved_count = 0
    
    for business in raw_prospects:
        # Vérification anti-doublon en BDD
        existing_lead = db.query(LeadModel).filter(
            LeadModel.company_name == business['company_name']
        ).first()
        
        if existing_lead:
            print(f"⏩ Déjà en BDD : {business['company_name']} (Ignoré)")
            continue
            
        # Qualification IA
        ai_analysis = qualifier.qualify_prospect(
            company_name=business['company_name'], 
            raw_data=business['raw_data']
        )
        
        if ai_analysis.should_contact:
            # Génération Email
            email = generator.generate_outreach_email(
                company_name=business['company_name'],
                business_type=ai_analysis.business_type,
                hook=ai_analysis.personalized_hook
            )
            
            # Enregistrement en BDD
            new_lead = LeadModel(
                company_name=business['company_name'],
                address=business.get('address'),
                phone=business.get('phone'),
                website=business.get('website'),
                energy_intensity=ai_analysis.energy_intensity,
                priority_score=ai_analysis.priority_score,
                personalized_hook=ai_analysis.personalized_hook,
                should_contact=ai_analysis.should_contact,
                email_subject=email.subject,
                email_body=email.body,
                status="QUALIFIED"
            )
            
            db.add(new_lead)
            db.commit()
            saved_count += 1
            print(f"💾 Sauvegardé en BDD : {business['company_name']} (Score: {ai_analysis.priority_score}/10)")
            
    db.close()
    print(f"\n✅ Terminé ! {saved_count} nouveaux leads insérés dans PostgreSQL.")
