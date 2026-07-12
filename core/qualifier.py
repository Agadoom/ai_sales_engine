import os
from typing import Literal
from pydantic import BaseModel, Field
from openai import OpenAI

# ==========================================
# 1. STRUCTURES DE SORTIE (Pydantic models)
# ==========================================

class ProspectQualification(BaseModel):
    company_name: str
    business_type: str
    energy_intensity: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        description="HIGH pour boulangeries, pressings, restos. LOW pour bureaux, pharmacies."
    )
    priority_score: int = Field(description="Score de 1 à 10 pour la priorité commerciale")
    personalized_hook: str = Field(description="Accroche personnalisée basée sur leur métier")
    should_contact: bool


class GeneratedEmail(BaseModel):
    subject: str = Field(description="L'objet de l'email de prospection")
    body: str = Field(description="Le corps complet de l'email rédigé")


# ==========================================
# 2. CLASSE DE QUALIFICATION (core/qualifier.py)
# ==========================================

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

        completion = self.client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=ProspectQualification,
        )

        return completion.choices[0].message.parsed


# ==========================================
# 3. CLASSE DE RÉDACTION D'EMAIL (clients/dedall_energy/generator.py)
# ==========================================

class EmailGenerator:
    def __init__(self, api_key: str = None):
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    def generate_outreach_email(self, company_name: str, hook: str) -> GeneratedEmail:
        system_prompt = """
Tu es un expert en Cold Email B2B pour Dedall Energy. 
Ton objectif est d'aider les entreprises gourmandes en énergie à réduire leurs factures d'électricité et de gaz.

RÈGLE DE RÉDACTION :
Rédige un email court (maximum 3-4 phrases), percutant, humain et sans jargon. Intègre l'accroche fournie.

RÈGLE DE SIGNATURE IMPÉRATIVE :
Termine TOUJOURS l'email exactement par cette formule de politesse et cette signature :
"Cordialement,
Billel de Dedall Energy"

Ne laisse JAMAIS de balises génériques ni de crochets comme [Votre Nom] ou [Votre Prénom].
"""

        user_prompt = f"""
        Entreprise cible : {company_name}
        Accroche personnalisée à intégrer : {hook}

        Rédige l'objet et le corps de l'email pour ce prospect.
        """

        completion = self.client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format=GeneratedEmail,
        )

        return completion.choices[0].message.parsed


# ==========================================
# 4. SCRIPT DE TEST DE BOUT EN BOUT
# ==========================================

if __name__ == "__main__":
    # Données brutes simulées (Ex: Scraping Google Maps)
    company = "Boulangerie Patisserie Cocol"
    prospect_data = "Boulangerie artisanale équipée de 3 grands fours électriques industriels ouverts 6j/7."

    print("--- 1. ÉTAPE DE QUALIFICATION ---")
    qualifier = ProspectQualifier()
    qualification = qualifier.qualify_prospect(company, prospect_data)
    
    print(f"Intensité Énergétique : {qualification.energy_intensity}")
    print(f"Score Commercial : {qualification.priority_score}/10")
    print(f"Accroche générée : {qualification.personalized_hook}\n")

    # Si le prospect est intéressant, on passe à la génération d'email
    if qualification.priority_score >= 7:
        print("--- 2. ÉTAPE DE RÉDACTION DE L'EMAIL ---")
        generator = EmailGenerator()
        email_complet = generator.generate_outreach_email(company, qualification.personalized_hook)
        
        print(f"Objet : {email_complet.subject}")
        print("-" * 40)
        print(email_complet.body)
        print("-" * 40)
    else:
        print("Prospect non prioritaire, aucun email rédigé.")
