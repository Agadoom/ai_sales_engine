import os
import time
import requests
import uvicorn
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

# Création des tables
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

class TriggerRequest(BaseModel):
    query: str = Field(..., json_schema_extra={"example": "Boulangerie Paris"})

class GeneratedEmail(BaseModel):
    subject: str
    body: str

# ==========================================
# 4. MODULE HEYGEN (Génération Vidéo)
# ==========================================

def get_french_voice_id(headers: dict) -> Optional[str]:
    """
    Interroge l'API HeyGen pour obtenir un voice_id français valide disponible sur le compte.
    """
    try:
        res = requests.get("https://api.heygen.com/v2/voices", headers=headers)
        if res.status_code == 200:
            voices = res.json().get("data", {}).get("voices", [])
            for v in voices:
                language = str(v.get("language", "")).lower()
                if "french" in language or "fr" in language:
                    print(f"🎙️ Voix française trouvée : {v.get('name')} ({v.get('voice_id')})")
                    return v.get("voice_id")
            if voices:
                print(f"⚠️ Pas de voix française spécifique trouvée, utilisation de : {voices[0].get('voice_id')}")
                return voices[0].get("voice_id")
    except Exception as e:
        print(f"⚠️ Erreur lors de la récupération des voix HeyGen : {e}")
    return None


def get_avatar_id(headers: dict) -> Optional[str]:
    """
    Interroge l'API HeyGen pour récupérer un avatar compatible sur l'espace de travail.
    """
    try:
        res = requests.get("https://api.heygen.com/v2/avatars", headers=headers)
        if res.status_code == 200:
            avatars = res.json().get("data", {}).get("avatars", [])
            if avatars:
                # 1. On cherche en priorité un look_id stable (souvent requis pour les avatars v2 custom)
                for av in avatars:
                    looks = av.get("looks", [])
                    if looks:
                        print(f"👤 Look d'avatar trouvé sur le compte : {looks[0].get('look_id')}")
                        return looks[0].get('look_id')
                
                # 2. Fallback sur l'avatar_id de base si pas de look_id imbriqué
                for av in avatars:
                    av_id = av.get("avatar_id", "")
                    if "expressive" not in av_id:
                        print(f"👤 Avatar standard trouvé sur le compte : {av_id}")
                        return av_id
                
                avatar_id = avatars[0].get("avatar_id")
                print(f"👤 Avatar sélectionné par défaut : {avatar_id}")
                return avatar_id
    except Exception as e:
        print(f"⚠️ Erreur lors de la récupération des avatars HeyGen : {e}")
    return None


def generate_heygen_video(company_name: str) -> Optional[str]:
    """
    Génère une vidéo personnalisée via l'API HeyGen V2 nettoyée et dynamique.
    """
    if not HEYGEN_API_KEY:
        print("⚠️ HEYGEN_API_KEY non fournie, étape vidéo ignorée.")
        return None

    headers = {
        "X-Api-Key": HEYGEN_API_KEY,
        "Content-Type": "application/json"
    }

    # Récupération dynamique des ressources valides de ton compte
    voice_id = get_french_voice_id(headers)
    avatar_id = get_avatar_id(headers)

    if not voice_id or not avatar_id:
        print("❌ Ressources HeyGen (Voix ou Avatar) introuvables. Annulation.")
        return None

    script_text = (
        f"Bonjour, je m'adresse à l'équipe de {company_name}. "
        f"En analysant vos équipements, j'ai remarqué une opportunité majeure "
        f"pour réduire vos factures d'électricité ce mois-ci. "
        f"Regardons ensemble comment Dedall Energy peut vous accompagner."
    )

    # Payload V2 propre et dynamique
    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": avatar_id,
                    "avatar_style": "normal"
                },
                "voice": {
                    "type": "text",
                    "input_text": script_text,
                    "voice_id": voice_id
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
        res = requests.post("https://api.heygen.com/v2/video/generate", json=payload, headers=headers)

        if res.status_code != 200:
            print(f"❌ Erreur HTTP HeyGen ({res.status_code}) : {res.text}")
            return None

        res_data = res.json()
        video_id = res_data.get("data", {}).get("video_id")

        if not video_id:
            print(f"❌ Réponse HeyGen sans video_id : {res_data}")
            return None

        print(f"🎬 Vidéo HeyGen lancée (ID: {video_id}). En attente du rendu...")

        status_url = f"https://api.heygen.com/v1/video_status.get?video_id={video_id}"
        for _ in range(30):
            time.sleep(10)
            status_res = requests.get(status_url, headers=headers).json()
            status = status_res.get("data", {}).get("status")

            if status == "completed":
                video_url = status_res["data"]["video_url"]
                print(f"✅ Vidéo HeyGen prête : {video_url}")
                return video_url
            elif status == "failed":
                print(f"❌ Échec de la génération vidéo HeyGen. Détails : {status_res}")
                return None

        print("⏰ Timeout HeyGen atteint.")
        return None

    except Exception as e:
        print(f"❌ Exception HeyGen détaillée : {e}")
        return None

# ==========================================
# 5. MODULE DE QUALIFICATION ET EMAIL (IA)
# ==========================================

def qualify_and_generate(company_name: str, raw_data: str):
    """
    Qualifie le prospect et rédige un email sur-mesure signé Billel de Dedall Energy.
    """
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
    db = SessionLocal()
    try:
        mock_leads = [
            {"company_name": f"{query} - Artisan 1", "raw_data": "Boulangerie équipée de 3 gros fours électriques 80kW."},
            {"company_name": f"{query} - Artisan 2", "raw_data": "Commerce de quartier, petit terminal de cuisson."}
        ]

        for item in mock_leads:
            company = item["company_name"]
            raw_info = item["raw_data"]

            qualif, email_info = qualify_and_generate(company, raw_info)

            v_url = None
            if qualif.priority_score >= 7:
                v_url = generate_heygen_video(company)

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
    version="2.0.0"
)

@app.get("/")
def home():
    return {"status": "online", "system": "Dedall Energy Engine v2"}

@app.post("/trigger-pipeline")
def trigger_pipeline(payload: TriggerRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_pipeline_task, payload.query)
    return {"message": f"Pipeline lancé en tâche de fond pour : '{payload.query}'"}

@app.post("/send-pending-leads")
def send_pending_leads(min_score: int = Query(7, ge=1, le=10)):
    if not OUTREACH_WEBHOOK_URL:
        raise HTTPException(status_code=500, detail="OUTREACH_WEBHOOK_URL non configurée.")

    db = SessionLocal()
    try:
        pending_leads = db.query(LeadModel).filter(
            LeadModel.status == "QUALIFIED",
            LeadModel.priority_score >= min_score
        ).all()

        if not pending_leads:
            return {"message": "Aucun lead en attente.", "sent_count": 0}

        sent_count = 0
        for lead in pending_leads:
            payload_webhook = {
                "company_name": lead.company_name,
                "email_subject": lead.email_subject,
                "email_body": lead.email_body,
                "video_url": lead.video_url,
                "priority_score": lead.priority_score
            }

            response = requests.post(OUTREACH_WEBHOOK_URL, json=payload_webhook)
            if response.status_code == 200:
                lead.status = "SENT"
                sent_count += 1

        db.commit()
        return {"message": "Traitement terminé", "sent_count": sent_count}

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

# ==========================================
# 8. ÉTAPE DE DÉMARRAGE DU SERVEUR
# ==========================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    print(f"🚀 Lancement d'Uvicorn sur le port {port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
