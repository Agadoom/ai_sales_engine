import os
import time
import json
import secrets
import requests
import uvicorn
from typing import List, Optional, Literal

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Request, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from openai import OpenAI

# ==========================================
# 1. AUTHENTIFICATION & SÉCURITÉ
# ==========================================

security = HTTPBasic()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "DedallEnergy2026!")

def get_current_user(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=401,
            detail="Identifiants incorrects",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

# ==========================================
# 2. CONFIGURATION ET TEMPLATES JINJA2
# ==========================================

templates = Jinja2Templates(directory="templates")

def escapejs_filter(val):
    if val is None:
        return ""
    return json.dumps(str(val))[1:-1]

templates.env.filters["escapejs"] = escapejs_filter

DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY")
OUTREACH_WEBHOOK_URL = os.getenv("OUTREACH_WEBHOOK_URL")

if not DATABASE_URL:
    raise ValueError("⚠️ DATABASE_URL est manquante dans les variables d'environnement.")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

client_openai = OpenAI(api_key=OPENAI_API_KEY)

# ==========================================
# 3. MODÈLE BASE DE DONNÉES (PostgreSQL)
# ==========================================

# ==========================================
# 3. MODÈLE BASE DE DONNÉES (PostgreSQL)
# ==========================================
from sqlalchemy import text

class LeadModel(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String, nullable=False)
    manager_name = Column(String, nullable=True)  # 👈 Nouvelle colonne
    phone = Column(String, nullable=True)
    raw_data = Column(Text, nullable=True)
    energy_intensity = Column(String, nullable=True)
    priority_score = Column(Integer, default=0)
    personalized_hook = Column(Text, nullable=True)
    email_subject = Column(String, nullable=True)
    email_body = Column(Text, nullable=True)
    video_url = Column(Text, nullable=True)
    status = Column(String, default="QUALIFIED")

# Crée la table si elle n'existe pas
Base.metadata.create_all(bind=engine)

# 🛠️ Migration automatique : Ajoute la colonne manager_name si elle n'existe pas encore
with engine.connect() as conn:
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS manager_name VARCHAR;"))
    conn.commit()

# ==========================================
# 4. STRUCTURES DE DONNÉES (Pydantic Models)
# ==========================================

class ProspectQualification(BaseModel):
    company_name: str
    energy_intensity: Literal["HIGH", "MEDIUM", "LOW"] = Field(
        description="HIGH pour boulangeries, pressings, restos. LOW pour bureaux."
    )
    priority_score: int = Field(description="Score de 1 à 10 pour la priorité commerciale")
    personalized_hook: str = Field(description="Accroche basée sur leur métier")

class TriggerRequest(BaseModel):
    query: Optional[str] = Field(None, json_schema_extra={"example": "Boulangerie Paris"})
    company_name: Optional[str] = None
    raw_data: Optional[str] = "Recherche via interface"

    @property
    def target_name(self) -> str:
        return self.query or self.company_name or "Entreprise Inconnue"

class GeneratedEmail(BaseModel):
    subject: str
    body: str

# ==========================================
# 5. MODULE D'ENRICHISSEMENT (APIs EXTERNES)
# ==========================================

def fetch_company_legal_info(search_query: str):
    """
    Interroge l'API gouvernementale Data.gouv pour trouver le gérant/dirigeant officiel.
    """
    print(f"🔍 Recherche d'enrichissement pour : {search_query}")
    manager_name = "Gérant(e)"
    full_company_name = search_query

    try:
        url = f"https://recherche-entreprises.api.gouv.fr/search?q={requests.utils.quote(search_query)}&per_page=1"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            results = res.json().get("results", [])
            if results:
                company_data = results[0]
                full_company_name = company_data.get("nom_complet", search_query)
                dirigeants = company_data.get("dirigeants", [])
                
                if dirigeants:
                    d = dirigeants[0]
                    prenom = d.get("prenoms", "").split(" ")[0].title()
                    nom = d.get("nom", "").title()
                    if prenom or nom:
                        manager_name = f"{prenom} {nom}".strip()
                        print(f"👤 Dirigeant trouvé : {manager_name}")
    except Exception as e:
        print(f"⚠️ Erreur lors de la recherche SIRENE/Data.gouv : {e}")

    return full_company_name, manager_name

# ==========================================
# 6. MODULE HEYGEN (Génération Vidéo)
# ==========================================

def get_french_voice_id(headers: dict) -> Optional[str]:
    try:
        res = requests.get("https://api.heygen.com/v2/voices", headers=headers)
        if res.status_code == 200:
            voices = res.json().get("data", {}).get("voices", [])
            for v in voices:
                language = str(v.get("language", "")).lower()
                if "french" in language or "fr" in language:
                    return v.get("voice_id")
            if voices:
                return voices[0].get("voice_id")
    except Exception as e:
        print(f"⚠️ Erreur Voix HeyGen : {e}")
    return None


def get_avatar_id(headers: dict) -> Optional[str]:
    try:
        res = requests.get("https://api.heygen.com/v2/avatars", headers=headers)
        if res.status_code == 200:
            avatars = res.json().get("data", {}).get("avatars", [])
            if avatars:
                for av in avatars:
                    looks = av.get("looks", [])
                    if looks:
                        return looks[0].get('look_id')
                for av in avatars:
                    if "expressive" not in av.get("avatar_id", ""):
                        return av.get("avatar_id")
                return avatars[0].get("avatar_id")
    except Exception as e:
        print(f"⚠️ Erreur Avatar HeyGen : {e}")
    return None


def generate_heygen_video(company_name: str, manager_name: str) -> Optional[str]:
    if not HEYGEN_API_KEY:
        return None

    headers = {
        "X-Api-Key": HEYGEN_API_KEY,
        "Content-Type": "application/json"
    }

    voice_id = get_french_voice_id(headers)
    avatar_id = get_avatar_id(headers)

    if not voice_id or not avatar_id:
        return None

    # Adaptation du script vidéo avec le nom du gérant s'il est connu
    salutation = f"Bonjour {manager_name}" if manager_name != "Gérant(e)" else f"Bonjour à l'équipe de {company_name}"

    script_text = (
        f"{salutation}. "
        f"En analysant les équipements de {company_name}, j'ai remarqué une opportunité majeure "
        f"pour réduire vos factures d'électricité ce mois-ci. "
        f"Regardons ensemble comment Dedall Energy peut vous accompagner."
    )

    payload = {
        "video_inputs": [
            {
                "character": {"type": "avatar", "avatar_id": avatar_id, "avatar_style": "normal"},
                "voice": {"type": "text", "input_text": script_text, "voice_id": voice_id},
                "background": {"type": "color", "value": "#FAFAFA"}
            }
        ],
        "dimension": {"width": 1280, "height": 720}
    }

    try:
        res = requests.post("https://api.heygen.com/v2/video/generate", json=payload, headers=headers)
        if res.status_code != 200:
            return None

        video_id = res.json().get("data", {}).get("video_id")
        if not video_id:
            return None

        status_url = f"https://api.heygen.com/v1/video_status.get?video_id={video_id}"
        for _ in range(30):
            time.sleep(10)
            status_res = requests.get(status_url, headers=headers).json()
            status = status_res.get("data", {}).get("status")

            if status == "completed":
                return status_res["data"]["video_url"]
            elif status == "failed":
                return None
        return None
    except Exception as e:
        print(f"❌ Exception HeyGen : {e}")
        return None

# ==========================================
# 7. MODULE DE QUALIFICATION ET EMAIL (IA)
# ==========================================

def qualify_and_generate(company_name: str, manager_name: str, raw_data: str):
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

    salutation_instruction = f"Adresse-toi directement à {manager_name}." if manager_name != "Gérant(e)" else "Adresse-toi au gérant de manière professionnelle."

    system_email = f"""
Tu es un expert en Cold Email B2B pour Dedall Energy. 
Rédige un email court (3-4 phrases), personnalisé et impactant.
{salutation_instruction}

RÈGLE IMPÉRATIVE DE SIGNATURE :
Termine TOUJOURS l'email exactement par cette signature :
Cordialement,
Benoît de Dedall Energy

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
# 8. PIPELINE PRINCIPAL ENRICHISSEMENT + IA
# ==========================================

def run_pipeline_task(query: str, raw_data: str):
    db = SessionLocal()
    try:
        # ÉAPE 1 : Enrichissement automatique (API Gérant / Data.gouv)
        company_name, manager_name = fetch_company_legal_info(query)

        # ÉTAPE 2 : Qualification + Email sur mesure
        qualif, email_info = qualify_and_generate(company_name, manager_name, raw_data)

        # ÉTAPE 3 : Vidéo personnalisée (si score >= 7)
        v_url = None
        if qualif.priority_score >= 7:
            v_url = generate_heygen_video(company_name, manager_name)

        # ÉTAPE 4 : Sauvegarde BDD
        lead = LeadModel(
            company_name=company_name,
            manager_name=manager_name,
            raw_data=raw_data,
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
        print(f"💾 Prospect enrichi & sauvegardé : {company_name} | Dirigeant : {manager_name} | Score : {qualif.priority_score}")

    except Exception as e:
        print(f"❌ Erreur pendant le pipeline : {e}")
        db.rollback()
    finally:
        db.close()

# ==========================================
# 9. ENDPOINTS FASTAPI
# ==========================================

app = FastAPI(
    title="Dedall Energy - Automated Outreach Engine",
    version="2.0.0"
)

@app.get("/")
def home(request: Request, username: str = Depends(get_current_user)):
    db = SessionLocal()
    try:
        leads = db.query(LeadModel).order_by(LeadModel.id.desc()).all()
        return templates.TemplateResponse(
            request=request, 
            name="dashboard.html", 
            context={"leads": leads}
        )
    finally:
        db.close()


@app.post("/trigger-pipeline")
def trigger_pipeline(payload: TriggerRequest, background_tasks: BackgroundTasks):
    company = payload.target_name
    background_tasks.add_task(run_pipeline_task, company, payload.raw_data)
    return {"message": f"Pipeline lancé en tâche de fond pour : '{company}'"}


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
                "manager_name": lead.manager_name,
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
# 10. DÉMARRAGE DU SERVEUR
# ==========================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    print(f"🚀 Lancement d'Uvicorn sur le port {port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
