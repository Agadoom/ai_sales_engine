import os
from typing import Literal
from pydantic import BaseModel, Field
from openai import OpenAI

# 1. Structure de sortie garantie par Pydantic
class ProspectQualification(BaseModel):
    company_name: str
    business_type: str
    energy_intensity: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        description="HIGH pour boulangeries, pressings, restos. LOW pour bureaux, pharmacies."
    )
    priority_score: int = Field(description="Score de 1 à 10 pour la priorité commerciale")
    personalized_hook: str = Field(description="Accroche personnalisée basée sur leur métier")
    should_contact: bool

# 2. Classe principale de qualification
class ProspectQualifier:
    def __init__(self, api_key: str = None):
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    def qualify_prospect(self, company_name: str, raw_data: str) -> ProspectQualification:
        system_prompt = (
            "Tu es un expert en qualification commerciale B2B pour un courtier en énergie. "
            "Ton rôle est d'analyser les données d'une entreprise et d'évaluer son potentiel "
            "d'économie d'électricité/gaz."
        )

        user_prompt = f"""
        Entreprise : {company_name}
        Données collectées : {raw_data}

        Analyse ce prospect et qualifie-le.
        """

        # Utilisation des Structured Outputs d'OpenAI pour garantir un JSON valide
        completion = self.client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=ProspectQualification,
        )

        return completion.choices[0].message.parsed


# --- TEST RAPIDE ---
if __name__ == "__main__":
    qualifier = ProspectQualifier()

    # Exemple : Scraping rapide d'une boulangerie
    prospect_data = "Boulangerie artisanale équipée de 3 fours électriques industriels ouverts 6j/7."
    
    result = qualifier.qualify_prospect("Boulangerie du Coin", prospect_data)
    
    print(f"Entreprise : {result.company_name}")
    print(f"Intensité Énergétique : {result.energy_intensity}")
    print(f"Score : {result.priority_score}/10")
    print(f"Accroche : {result.personalized_hook}")
