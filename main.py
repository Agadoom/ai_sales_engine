import os
import time
import json
import secrets
import requests
import uvicorn
from datetime import datetime, timedelta
from typing import List, Optional, Literal

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Request, Depends
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from openai import OpenAI
from sqlalchemy import Column, Integer, String, Text, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from database import Base  # Ou l'import correspondant à ton fichier database.py


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

# 1. LA TABLE UTILISATEUR (SaaS)
class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    
    # Gestion de l'abonnement
    is_active = Column(Boolean, default=True)
    subscription_status = Column(String, default="trial")  # trial, active, canceled
    stripe_customer_id = Column(String, nullable=True)
    
    # Clés API personnelles de l'utilisateur (optionnel, s'il utilise ses propres comptes)
    openai_api_key = Column(String, nullable=True)
    hunter_api_key = Column(String, nullable=True)
    heygen_api_key = Column(String, nullable=True)

    # Relation : Un utilisateur a plusieurs leads
    leads = relationship("LeadModel", back_populates="owner", cascade="all, delete-orphan")


# 2. LA TABLE LEADS (Mise à jour avec liaison utilisateur)
class LeadModel(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    
    # 🔑 LA CLÉ ÉTRANGÈRE : Lie le lead à un utilisateur précis
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    
    company_name = Column(String, nullable=False)
    manager_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    email = Column(String, nullable=True)
    address = Column(String, nullable=True)
    raw_data = Column(Text, nullable=True)
    energy_intensity = Column(String, nullable=True)
    priority_score = Column(Integer, default=0)
    personalized_hook = Column(Text, nullable=True)
    email_subject = Column(String, nullable=True)
    email_body = Column(Text, nullable=True)
    video_url = Column(Text, nullable=True)
    status = Column(String, default="QUALIFIED")

    # Relation inverse : Le lead appartient à un utilisateur
    owner = relationship("UserModel", back_populates="leads")

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


Base.metadata.create_all(bind=engine)

# Auto-migrations SQL
with engine.connect() as conn:
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS manager_name VARCHAR;"))
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS phone VARCHAR;"))
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS email VARCHAR;"))
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS address VARCHAR;"))
    conn.commit()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

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
    query: Optional[str] = Field(None, json_schema_extra={"example": "Boulangerie Lyon"})
    company_name: Optional[str] = None
    raw_data: Optional[str] = "Recherche via interface"

    @property
    def target_name(self) -> str:
        return self.query or self.company_name or "Entreprise Inconnue"

class GeneratedEmail(BaseModel):
    subject: str
    body: str

# ==========================================
# 5. MODULE D'ENRICHISSEMENT & FILTRES
# ==========================================

def is_older_than_3_years(date_string: Optional[str]) -> bool:
    """Vérifie si l'entreprise a plus de 3 ans d'ancienneté."""
    if not date_string:
        return True
    try:
        creation_date = datetime.strptime(date_string, "%Y-%m-%d")
        three_years_ago = datetime.now() - timedelta(days=3*365)
        return creation_date <= three_years_ago
    except Exception:
        return True

def fetch_manager_email_hunter(manager_name: str, company_name: str) -> Optional[str]:
    """Recherche l'e-mail du gérant via Hunter.io."""
    if not HUNTER_API_KEY:
        print("⚠️ HUNTER_API_KEY non configurée.")
        return None

    parts = manager_name.split(" ")
    first_name = parts[0] if len(parts) > 0 else ""
    last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    try:
        url = (
            f"https://api.hunter.io/v2/email-finder?"
            f"company={requests.utils.quote(company_name)}"
            f"&first_name={requests.utils.quote(first_name)}"
            f"&last_name={requests.utils.quote(last_name)}"
            f"&api_key={HUNTER_API_KEY}"
        )
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            data = res.json().get("data", {})
            email = data.get("email")
            if email:
                print(f"📧 Email trouvé via Hunter.io : {email}")
                return email
    except Exception as e:
        print(f"⚠️ Erreur Hunter.io : {e}")

    return None

def fetch_company_legal_info(search_query: str, limit: int = 5):
    """
    Recherche Data.gouv ciblée Rhône-Alpes + Filtre +3 ans d'ancienneté.
    """
    print(f"🔍 Recherche ciblée Rhône-Alpes (+3 ans) pour : '{search_query}'")
    prospects = []
    
    # Départements Rhône-Alpes : 69, 38, 42, 01, 73, 74, 26, 07
    rhone_alpes_deps = "69,38,42,01,73,74,26,07"

    try:
        url = (
            f"https://recherche-entreprises.api.gouv.fr/search?"
            f"q={requests.utils.quote(search_query)}"
            f"&departement={rhone_alpes_deps}"
            f"&per_page=15"
        )
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            results = res.json().get("results", [])
            for company_data in results:
                # Filtre : +3 ans d'ancienneté
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

                # Recherche E-mail via Hunter.io
                email = fetch_manager_email_hunter(manager_name, company_name)

                prospects.append({
                    "company_name": company_name,
                    "manager_name": manager_name,
                    "address": address,
                    "email": email,
                    "phone": None
                })

                if len(prospects) >= limit:
                    break

    except Exception as e:
        print(f"⚠️ Erreur Data.gouv : {e}")

    if not prospects:
        prospects.append({
            "company_name": search_query, 
            "manager_name": "Gérant(e)", 
            "address": "Rhône-Alpes",
            "email": None,
            "phone": None
        })

    return prospects

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
    # 1. Étape de qualification inchangée
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

    # 2. Étape Email : On force une structure HTML ultra-pro et moderne
    salutation_instruction = f"Adresse-toi directement à {manager_name}." if manager_name != "Gérant(e)" else "Adresse-toi au gérant de manière professionnelle."

    system_email = f"""
Tu es un expert en Cold Email B2B et en intégration HTML responsive.
Rédige un email de prospection court (3-4 phrases), ultra-personnalisé et percutant.
{salutation_instruction}

Génère IMPÉRATIVEMENT le corps du message (body) au format HTML moderne (inline CSS). 
Utilise une mise en page épurée (fond blanc, texte gris foncé #333333, police sans-serif comme Arial, interligne aéré de 1.6).

Intègre une structure claire avec des paragraphes séparés (<p style="margin-bottom: 16px;">).

RÈGLE IMPÉRATIVE DE SIGNATURE :
Termine TOUJOURS l'email exactement par cette signature en HTML propre :
<p style="margin-top: 24px; margin-bottom: 0;">Cordialement,</p>
<p style="font-weight: bold; color: #1e3a8a; margin: 0;">Benoît</p>
<p style="font-size: 12px; color: #666666; margin: 0;">Expert Solutions Énergie — Dedall Energy</p>

Ne mets JAMAIS de texte brut de type "--- sent with...", pas de crochets, pas de balises markdown (comme ```html), renvoie directement le code HTML propre.
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
        # Récupère les prospects filtrés (+3 ans / Rhône-Alpes) avec email Hunter.io
        prospects = fetch_company_legal_info(query, limit=5)
        print(f"🎯 {len(prospects)} prospect(s) qualifié(s) dans le secteur Rhône-Alpes (+3 ans)")

        for p in prospects:
            company_name = p["company_name"]
            manager_name = p["manager_name"]
            manager_email = p.get("email")

            print(f"\n⚡ Traitement de : {company_name} (Dirigeant : {manager_name})")

            # Qualification + Génération d'email
            qualif, email_info = qualify_and_generate(company_name, manager_name, raw_data)

            # Vidéo HeyGen si le score est suffisant
            v_url = None
            if qualif.priority_score >= 7:
                v_url = generate_heygen_video(company_name, manager_name)

            # Sauvegarde en BDD
            lead = LeadModel(
                company_name=company_name,
                manager_name=manager_name,
                email=manager_email,
                address=p.get("address"),
                phone=p.get("phone"),
                raw_data=raw_data,
                energy_intensity=qualif.energy_intensity,
                priority_score=qualif.priority_score,
                personalized_hook=qualif.personalized_hook,
                email_subject=email_info.subject,
                email_body=email_info.body,
                video_url=v_url,
                status="QUALIFIED" # 🛡️ Statut conservé pour validation manuelle
            )
            db.add(lead)
            db.commit()
            print(f"💾 Prospect sauvegardé : {company_name} | Mail : {manager_email}")

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
def home(request: Request, db: Session = Depends(get_db)):
    try:
        # 1. On récupère les leads depuis la BDD
        raw_leads = db.query(LeadModel).order_by(LeadModel.id.desc()).all()
        
        # 2. On les convertit tous en dictionnaires sérialisables en JSON
        leads_dict = [lead.to_dict() for lead in raw_leads]
        
        # 3. On envoie la liste nettoyée au template
        return templates.TemplateResponse(
            request=request, 
            name="dashboard.html", 
            context={"leads": leads_dict}
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
                "email": lead.email,
                "phone": lead.phone,
                "address": lead.address,
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
        return {"message": "Envoi terminé avec succès", "sent_count": sent_count}

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
