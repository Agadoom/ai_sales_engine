# clients/dedall_energy/pipeline.py
import os
import sys
import json
from pathlib import Path

# Ces lignes permettent à Python de trouver les modules dans le dossier racine
sys.path.append(str(Path(__file__).resolve().parents[2]))

from core.scraper import LocalBusinessScraper
from core.qualifier import ProspectQualifier

def run_dedall_pipeline(query: str):
    print(f"🚀 Démarrage du pipeline Dedall Energy pour la recherche : '{query}'")
    
    # 1. Initialisation des moteurs
    scraper = LocalBusinessScraper()
    qualifier = ProspectQualifier()
    
    # 2. Sourcing
    raw_prospects = scraper.search_businesses(query)
    print(f"📋 {len(raw_prospects)} prospects trouvés. Lancement de la qualification IA...")
    
    qualified_leads = []
    
    # 3. Qualification par l'IA
    for business in raw_prospects:
        print(f"🧠 Analyse de : {business['company_name']}...")
        try:
            # L'IA analyse les données collectées par le scraper
            ai_analysis = qualifier.qualify_prospect(
                company_name=business['company_name'], 
                raw_data=business['raw_data']
            )
            
            # On fusionne les infos de contact et l'analyse de l'IA
            full_lead = {
                **business,
                "energy_intensity": ai_analysis.energy_intensity,
                "priority_score": ai_analysis.priority_score,
                "personalized_hook": ai_analysis.personalized_hook,
                "should_contact": ai_analysis.should_contact
            }
            qualified_leads.append(full_lead)
            
        except Exception as e:
            print(f"⚠️ Impossible de qualifier {business['company_name']}: {e}")
            
    # 4. Sauvegarde des résultats
    output_file = Path(__file__).parent / "output_leads.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(qualified_leads, f, ensure_ascii=False, indent=4)
        
    print(f"\n✅ Terminé ! {len(qualified_leads)} leads sauvegardés dans {output_file.name}")

if __name__ == "__main__":
    # Configure tes clés API avant de lancer
    # os.environ["GOOGLE_PLACES_API_KEY"] = "..."
    # os.environ["OPENAI_API_KEY"] = "..."
    
    run_dedall_pipeline("Boulangerie Lille")
