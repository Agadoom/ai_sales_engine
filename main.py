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
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from openai import OpenAI
from datetime import datetime, timedelta

# ==========================================
# 1. AUTHENTIFICATION & SÉCURITÉ
# ==========================================

security = HTTPBasic()

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "DedallEnergy2026!")

HUNTER_API_KEY = os.getenv("HUNTER_API_KEY")


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

class LeadModel(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String, nullable=False)
    manager_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)        # 👈 Téléphone
    email = Column(String, nullable=True)        # 👈 E-mail du gérant
    address = Column(String, nullable=True)      # 👈 Adresse physique
    raw_data = Column(Text, nullable=True)
    energy_intensity = Column(String, nullable=True)
    priority_score = Column(Integer, default=0)
    personalized_hook = Column(Text, nullable=True)
    email_subject = Column(String, nullable=True)
    email_body = Column(Text, nullable=True)
    video_url = Column(Text, nullable=True)
    status = Column(String, default="QUALIFIED")

# Auto-migration SQL pour ajouter les colonnes manquantes sans casser la BDD
with engine.connect() as conn:
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS phone VARCHAR;"))
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS email VARCHAR;"))
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS address VARCHAR;"))
    conn.commit()


# Dépendance pour la session de base de données
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def is_older_than_3_years(date_string: str) -> bool:
    """Vérifie si l'entreprise a plus de 3 ans."""
    if not date_string:
        return True
    try:
        creation_date = datetime.strptime(date_string, "%Y-%m-%d")
        three_years_ago = datetime.now() - timedelta(days=3*365)
        return creation_date <= three_years_ago
    except Exception:
        return True

def fetch_company_legal_info(search_query: str, limit: int = 5):
    """
    Recherche Data.gouv ciblée Rhône-Alpes et filtrée sur +3 ans d'ancienneté.
    """
    print(f"🔍 Recherche ciblée Rhône-Alpes pour : '{search_query}'")
    prospects = []
    
    # Départements Rhône-Alpes : 69, 38, 42, 01, 73, 74, 26, 07
    rhone_alpes_deps = "69,38,42,01,73,74,26,07"

    try:
        url = (
            f"https://recherche-entreprises.api.gouv.fr/search?"
            f"q={requests.utils.quote(search_query)}"
            f"&departement={rhone_alpes_deps}"
            f"&per_page=15" # On prend une marge pour filtrer les < 3 ans
        )
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            results = res.json().get("results", [])
            for company_data in results:
                # 1. Filtre Ancienneté 3 ans
                date_creation = company_data.get("date_creation")
                if not is_older_than_3_years(date_creation):
                    continue

                company_name = company_data.get("nom_complet", search_query)
                address = company_data.get("adresse", "Rhône-Alpes")
                manager_name = "Gérant(e)"
                dirigeants = company_data.get("dirigeants", [])

                if dirigeants:
                    d = dirigeants[0]
                    prenom = d.get("prenoms", "").split(" ")[0].title()
                    nom = d.get("nom", "").title()
                    if prenom or nom:
                        manager_name = f"{prenom} {nom}".strip()

                prospects.append({
                    "company_name": company_name,
                    "manager_name": manager_name,
                    "address": address,
                    # Ces champs seront complétés par l'API de contact (ex: Dropcontact / Hunter)
                    "email": None,
                    "phone": None
                })

                if len(prospects) >= limit:
                    break

    except Exception as e:
        print(f"⚠️ Erreur Data.gouv : {e}")

    return prospects

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

def fetch_company_legal_info(search_query: str, limit: int = 5):
    """
    Interroge l'API Data.gouv et retourne une liste de prospects (jusqu'à `limit`).
    """
    print(f"🔍 Recherche multi-prospects pour : '{search_query}' (max {limit})")
    prospects = []

    try:
        url = f"https://recherche-entreprises.api.gouv.fr/search?q={requests.utils.quote(search_query)}&per_page={limit}"
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            results = res.json().get("results", [])
            for company_data in results:
                company_name = company_data.get("nom_complet", search_query)
                manager_name = "Gérant(e)"
                dirigeants = company_data.get("dirigeants", [])

                if dirigeants:
                    d = dirigeants[0]
                    prenom = d.get("prenoms", "").split(" ")[0].title()
                    nom = d.get("nom", "").title()
                    if prenom or nom:
                        manager_name = f"{prenom} {nom}".strip()

                prospects.append({
                    "company_name": company_name,
                    "manager_name": manager_name
                })
    except Exception as e:
        print(f"⚠️ Erreur lors de la recherche Data.gouv : {e}")

    # Fallback si rien n'est trouvé
    if not prospects:
        prospects.append({"company_name": search_query, "manager_name": "Gérant(e)"})

    return prospects


def fetch_manager_email_dropcontact(manager_name: str, company_name: str) -> Optional[str]:
    """
    Interroge l'API Dropcontact pour retrouver l'email nominatif du gérant.
    """
    if not DROPCONTACT_API_KEY:
        print("⚠️ DROPCONTACT_API_KEY non configurée.")
        return None

    headers = {
        "X-Access-Token": DROPCONTACT_API_KEY,
        "Content-Type": "application/json"
    }

    # Découpage rapide du prénom/nom s'il est connu
    parts = manager_name.split(" ")
    first_name = parts[0] if len(parts) > 0 else ""
    last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    payload = {
        "data": [
            {
                "first_name": first_name,
                "last_name": last_name,
                "company": company_name
            }
        ]
    }

    try:
        # Envoi de la demande d'enrichissement
        res = requests.post("https://api.dropcontact.io/batch", json=payload, headers=headers)
        if res.status_code == 200:
            request_id = res.json().get("request_id")
            
            # Dropcontact traite la demande de manière asynchrone (attente de 3-5 secondes)
            for _ in range(5):
                time.sleep(3)
                poll_res = requests.get(f"https://api.dropcontact.io/batch/{request_id}", headers=headers)
                if poll_res.status_code == 200:
                    data = poll_res.json()
                    if data.get("success"):
                        results = data.get("data", [])
                        if results and "email" in results[0]:
                            email = results[0]["email"][0].get("email")
                            print(f"📧 Email trouvé via Dropcontact : {email}")
                            return email
    except Exception as e:
        print(f"⚠️ Erreur lors de l'appel Dropcontact : {e}")

    return None


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
        # 1. Filtre Rhône-Alpes + 3 ans d'existence
        prospects = fetch_company_legal_info(query, limit=5)
        print(f"🎯 {len(prospects)} prospect(s) trouvé(s)")

        for p in prospects:
            company_name = p["company_name"]
            manager_name = p["manager_name"]

            # 2. Qualification + Email sur-mesure (OpenAI)
            qualif, email_info = qualify_and_generate(company_name, manager_name, raw_data)

            # 3. Génération Vidéo HeyGen (si score >= 7)
            v_url = None
            if qualif.priority_score >= 7:
                v_url = generate_heygen_video(company_name, manager_name)

            # 4. Enrichissement Email via Dropcontact
            manager_email = fetch_manager_email_dropcontact(manager_name, company_name)

            # 5. Sauvegarde en BDD (Statut "QUALIFIED" = EN ATTENTE DE TON ACCORD)
            lead = LeadModel(
                company_name=company_name,
                manager_name=manager_name,
                email=manager_email, # 👈 Stocké ici
                address=p.get("address"),
                raw_data=raw_data,
                energy_intensity=qualif.energy_intensity,
                priority_score=qualif.priority_score,
                personalized_hook=qualif.personalized_hook,
                email_subject=email_info.subject,
                email_body=email_info.body,
                video_url=v_url,
                status="QUALIFIED" # 🛡️ Bloqué ici : AUCUN ENVOI AUTOMATIQUE
            )
            db.add(lead)
            db.commit()
            print(f"💾 Prospect prêt pour révision : {company_name} | Mail : {manager_email}")

    except Exception as e:
        print(f"❌ Erreur pipeline : {e}")
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

# 🔓 PROTECTION DESACTIVÉE ICI POUR LE TEST PILOTE
@app.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    try:
        leads = db.query(LeadModel).order_by(LeadModel.id.desc()).all()
        return templates.TemplateResponse(
            request=request, 
            name="dashboard.html", 
            context={"leads": leads}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
