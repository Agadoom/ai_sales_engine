import os
import time
import requests
from typing import List, Optional, Literal
from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from openai import OpenAI

# ==========================================
# 1. CONFIGURATION ET VARIABLES D'ENVIRONNEMENT
# ==========================================

DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY")
OUTREACH_WEBHOOK_URL = os.getenv("OUTREACH_WEBHOOK_URL")

if not DATABASE_URL:
    raise ValueError("⚠️ DATABASE_URL est manquante dans les variables d'environnement.")

# Connexion PostgreSQL via SQLAlchemy
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

client_openai = OpenAI(api_key=OPENAI_API_KEY)

# ==========================================
# 2. MODÈLE BASE DE DONNÉES (PostgreSQL)
# ==========================================

class LeadModel(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    raw_data = Column(Text, nullable=True)
    energy_intensity = Column(String, nullable=True)
    priority_score = Column(Integer, default=0)
    personalized_hook = Column(Text, nullable=True)
    email_subject = Column(String, nullable=True)
    email_body = Column(Text, nullable=True)
    video_url = Column(Text, nullable=True)
    status = Column(String, default="QUALIFIED")  # QUALIFIED, SENT, FAILED

#  LIGNE CORRIGÉE :
Base.metadata.create_all(bind=engine)

# ==========================================
# 3. STRUCTURES DE DONNÉES (Pydantic Models)
# ==========================================

class ProspectQualification(BaseModel):
    company_name: str
    energy_intensity: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        description="HIGH pour boulangeries, pressings, restos. LOW pour bureaux."
    )
    priority_score: int = Field(description="Score de 1 à 10 pour la priorité commerciale")
    personalized_hook: str = Field(description="Accroche basée sur leur métier")

class GeneratedEmail(BaseModel):
    subject: str
    body: str

# ✅ NOUVELLE VERSION (Propre)
class TriggerRequest(BaseModel):
    query: str = Field(..., json_schema_extra={"example": "Boulangerie Paris"})


# ==========================================
# 4. MODULE HEYGEN (Génération Vidéo)
# ==========================================

def generate_heygen_video(company_name: str) -> Optional[str]:
    """
    Génère une vidéo personnalisée via HeyGen V2 et retourne l'URL vidéo finale.
    """
    if not HEYGEN_API_KEY:
        print("⚠️ HEYGEN_API_KEY non fournie, étape vidéo ignorée.")
        return None

    headers = {
        "X-Api-Key": HEYGEN_API_KEY,
        "Content-Type": "application/json"
    }

    script_text = (
        f"Bonjour, je m'adresse à l'équipe de {company_name}. "
        f"En analysant vos équipements, j'ai remarqué une opportunité majeure "
        f"pour réduire vos factures d'électricité ce mois-ci. "
        f"Regardons ensemble comment Dedall Energy peut vous accompagner."
    )

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
                    "voice_id": "0dfbd6516a504c5bb17fa487a329df99"  # Voix française HD (Henri)
                },
                "background": {
                    "type": "color",
                    "value": "#FAFAFA"
                }
            }
        ],
        "dimension": {"width": 1280, "height": 720}
    }

    try:
        # 1. Lancement du rendu vidéo
        res = requests.post("https://api.heygen.com/v2/video/generate", json=payload, headers=headers)
        res_data = res.json()
        video_id = res_data.get("data", {}).get("video_id")

        if not video_id:
            print(f"❌ Erreur HeyGen lors de l'initialisation : {res_data}")
            return None

        print(f"🎬 Vidéo HeyGen lancée (ID: {video_id}). En attente du rendu...")

        # 2. Polling (Attente que la vidéo soit prête)
        status_url = f"https://api.heygen.com/v1/video_status.get?video_id={video_id}"
        for _ in range(20):  # Attente max : 20 x 10s = 200s
            time.sleep(10)
            status_res = requests.get(status_url, headers=headers).json()
            status = status_res.get("data", {}).get("status")

            if status == "completed":
                video_url = status_res["data"]["video_url"]
                print(f"✅ Vidéo HeyGen prête : {video_url}")
                return video_url
            elif status == "failed":
                print("❌ Échec de la génération vidéo HeyGen.")
                return None

        print("⏰ Timeout HeyGen atteint.")
        return None

    except Exception as e:
        print(f"❌ Exception HeyGen : {e}")
        return None

# ==========================================
# 5. MODULE DE QUALIFICATION ET EMAIL (IA)
# ==========================================

def qualify_and_generate(company_name: str, raw_data: str):
    """
    Qualifie le prospect et rédige un email sur-mesure signé Billel de Dedall Energy.
    """
    # Étape 1 : Qualification et scoring
    system_qualif = (
        "Tu es un expert en qualification B2B pour courtier en énergie. "
        "Évalue le potentiel d'économie d'énergie de l'entreprise."
    )
    user_qualif = f"Entreprise : {company_name}\nDonnées brutes : {raw_data}"

    res_qualif = client_openai.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_qualif},
            {"role": "user", "content": user_qualif}
        ],
        response_format=ProspectQualification
    )
    qualification = res_qualif.choices[0].message.parsed

    # Étape 2 : Rédaction de l'email
    system_email = """
Tu es un expert en Cold Email B2B pour Dedall Energy. 
Rédige un email court (3-4 phrases), personnalisé et impactant.

RÈGLE IMPÉRATIVE DE SIGNATURE :
Termine TOUJOURS l'email exactement par cette signature :
Cordialement,
Billel de Dedall Energy

Ne mets JAMAIS de crochets comme [Votre Nom] ni d'autres balises génériques.
"""
    user_email = f"Entreprise : {company_name}\nAccroche : {qualification.personalized_hook}"

    res_email = client_openai.beta.chat.completions.parse(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_email},
            {"role": "user", "content": user_email}
        ],
        response_format=GeneratedEmail
    )
    email_data = res_email.choices[0].message.parsed

    return qualification, email_data

# ==========================================
# 6. PIPELINE PRINCIPAL (Background Task)
# ==========================================

def run_pipeline_task(query: str):
    """
    Tâche de fond : Recherche, Qualification, Génération vidéo et Sauvegarde en BDD.
    """
    db = SessionLocal()
    try:
        # Simulation/Exemple de données récoltées (À relier à ton scraper Google Maps)
        mock_leads = [
            {"company_name": f"{query} - Artisan 1", "raw_data": "Boulangerie équipée de 3 gros fours électriques 80kW."},
            {"company_name": f"{query} - Artisan 2", "raw_data": "Commerce de quartier, petit terminal de cuisson."}
        ]

        for item in mock_leads:
            company = item["company_name"]
            raw_info = item["raw_data"]

            # 1. Qualification + Email IA
            qualif, email_info = qualify_and_generate(company, raw_info)

            # 2. Génération vidéo HeyGen (Seulement si le score est haut)
            v_url = None
            if qualif.priority_score >= 7:
                v_url = generate_heygen_video(company)

            # 3. Sauvegarde dans PostgreSQL
            lead = LeadModel(
                company_name=company,
                raw_data=raw_info,
                energy_intensity=qualif.energy_intensity,
                priority_score=qualif.priority_score,
                personalized_hook=qualif.personalized_hook,
                email_subject=email_info.subject,
                email_body=email_info.body,
                video_url=v_url,
                status="QUALIFIED"
            )
            db.add(lead)
            db.commit()
            print(f"💾 Prospect sauvegardé en BDD : {company} (Score: {qualif.priority_score})")

    except Exception as e:
        print(f"❌ Erreur pendant le pipeline : {e}")
        db.rollback()
    finally:
        db.close()

# ==========================================
# 7. ENDPOINTS FASTAPI (Swagger)
# ==========================================

app = FastAPI(
    title="Dedall Energy - Automated Outreach Engine",
    description="API de qualification de prospects, génération d'emails & vidéos IA et dispatch vers n8n.",
    version="2.0.0"
)

@app.get("/")
def home():
    return {"status": "online", "system": "Dedall Energy Engine v2"}

@app.post("/trigger-pipeline")
def trigger_pipeline(payload: TriggerRequest, background_tasks: BackgroundTasks):
    """
    Déclenche le scraping, la qualification IA et la vidéo en tâche de fond.
    """
    background_tasks.add_task(run_pipeline_task, payload.query)
    return {"message": f"Pipeline lancé en tâche de fond pour la recherche : '{payload.query}'"}

@app.post("/send-pending-leads")
def send_pending_leads(min_score: int = Query(7, ge=1, le=10)):
    """
    Récupère les leads qualifiés en BDD et les envoie au Webhook n8n.
    """
    if not OUTREACH_WEBHOOK_URL:
        raise HTTPException(status_code=500, detail="OUTREACH_WEBHOOK_URL non configurée.")

    db = SessionLocal()
    try:
        # Sélection des leads qualifiés ayant un score suffisant
        pending_leads = db.query(LeadModel).filter(
            LeadModel.status == "QUALIFIED",
            LeadModel.priority_score >= min_score
        ).all()

        if not pending_leads:
            return {"message": "Aucun lead qualifié en attente d'envoi.", "sent_count": 0}

        sent_count = 0
        for lead in pending_leads:
            payload_webhook = {
                "company_name": lead.company_name,
                "email_subject": lead.email_subject,
                "email_body": lead.email_body,
                "video_url": lead.video_url,  # Transmis directement à n8n !
                "priority_score": lead.priority_score
            }

            response = requests.post(OUTREACH_WEBHOOK_URL, json=payload_webhook)

            if response.status_code == 200:
                lead.status = "SENT"
                sent_count += 1
            else:
                print(f"❌ Erreur d'envoi webhook ({response.status_code}) pour {lead.company_name}")

        db.commit()
        return {"message": "Traitement terminé", "sent_count": sent_count}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()
