# core/generator.py
import os
from pydantic import BaseModel, Field
from openai import OpenAI

class GeneratedEmail(BaseModel):
    subject: str = Field(description="Objet d'email accrocheur, très court (3 à 5 mots)")
    body: str = Field(description="Corps de l'email, direct, personnalisé et orienté valeur")

class EmailGenerator:
    def __init__(self, api_key: str = None):
        self.client = OpenAI(api_key=api_key or os.getenv("OPENAI_API_KEY"))

    def generate_outreach_email(self, company_name: str, business_type: str, hook: str) -> GeneratedEmail:
        system_prompt = (
            "Tu es un expert en Cold Email B2B pour Dedall Energy. "
            "Rédige un email très court (moins de 100 mots), naturel et personnalisé. "
            "Ne fais pas de vente hard. L'objectif est d'ouvrir la discussion sur l'optimisation des coûts d'énergie."
        )

        user_prompt = f"""
        Entreprise : {company_name}
        Secteur : {business_type}
        Accroche / Élément de contexte : {hook}

        Rédige un objet et un message adaptés.
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
