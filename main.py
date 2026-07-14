import os
import time
import json
import requests
import uvicorn
import jwt
from datetime import datetime, timedelta
from typing import List, Optional, Literal

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Request, Depends, status, Form, Response
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordBearer
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from passlib.context import CryptContext

from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, text, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

# ==========================================
# 1. INITIALISATION DE FASTAPI & JINJA2
# ==========================================

app = FastAPI(
    title="Dedall Energy - Automated Outreach Engine",
    version="2.0.0"
)

templates = Jinja2Templates(directory="templates")

def escapejs_filter(val):
    if val is None:
        return ""
    return json.dumps(str(val))[1:-1]

templates.env.filters["escapejs"] = escapejs_filter

# ==========================================
# 2. CONFIGURATION BASE DE DONNÉES & API KEYS
# ==========================================

DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY")
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY")
OUTREACH_WEBHOOK_URL = os.getenv("OUTREACH_WEBHOOK_URL")

if not DATABASE_URL:
    raise ValueError("⚠️ DATABASE_URL est manquante dans les variables d'environnement.")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

client_openai = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================================
# 3. MODÈLES BASE DE DONNÉES (SQLAlchemy)
# ==========================================

class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    
    is_active = Column(Boolean, default=True)
    subscription_status = Column(String, default="trial")  # trial, active, canceled
    stripe_customer_id = Column(String, nullable=True)
    
    openai_api_key = Column(String, nullable=True)
    hunter_api_key = Column(String, nullable=True)
    heygen_api_key = Column(String, nullable=True)

    leads = relationship("LeadModel", back_populates="owner", cascade="all, delete-orphan")


class LeadModel(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
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

    owner = relationship("UserModel", back_populates="leads")

    def to_dict(self):
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


Base.metadata.create_all(bind=engine)

# Auto-migrations
with engine.connect() as conn:
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS manager_name VARCHAR;"))
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS phone VARCHAR;"))
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS email VARCHAR;"))
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS address VARCHAR;"))
    conn.commit()

# ==========================================
# 4. SÉCURITÉ & AUTHENTIFICATION (JWT)
# ==========================================

SECRET_KEY = os.getenv("JWT_SECRET_KEY", "SECRET_KEY_DEDALL_2026_CHANGE_ME")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    if not token:
        return None
    if token.startswith("Bearer "):
        token = token.split(" ")[1]
        
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            return None
    except jwt.PyJWTError:
        return None
        
    return db.query(UserModel).filter(UserModel.email == email).first()

# ==========================================
# 5. STRUCTURES DE DONNÉES (Pydantic Models)
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
# 6. MODULE D'ENRICHISSEMENT & FILTRES
# ==========================================

def is_older_than_3_years(date_string: Optional[str]) -> bool:
    if not date_string:
        return True
    try:
        creation_date = datetime.strptime(date_string, "%Y-%m-%d")
        three_years_ago = datetime.now() - timedelta(days=3*365)
        return creation_date <= three_years_ago
    except Exception:
        return True

def fetch_manager_email_hunter(manager_name: str, company_name: str) -> Optional[str]:
    if not HUNTER_API_KEY:
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
            return data.get("email")
    except Exception as e:
        print(f"⚠️ Erreur Hunter.io : {e}")

    return None

def fetch_company_legal_info(search_query: str, limit: int = 5):
    prospects = []
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
# 7. MODULE HEYGEN (Génération Vidéo)
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
# 8. QUALIFICATION & EMAIL (IA)
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

Ne mets JAMAIS de texte brut de type "--- sent with...", pas de crochets, pas de balises markdown, renvoie directement le code HTML propre.
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
# 9. PIPELINE AVEC USER_ID
# ==========================================

def run_pipeline_task(user_id: int, query: str, raw_data: str):
    db = SessionLocal()
    try:
        prospects = fetch_company_legal_info(query, limit=5)

        for p in prospects:
            company_name = p["company_name"]
            manager_name = p["manager_name"]
            manager_email = p.get("email")

            qualif, email_info = qualify_and_generate(company_name, manager_name, raw_data)

            v_url = None
            if qualif.priority_score >= 7:
                v_url = generate_heygen_video(company_name, manager_name)

            lead = LeadModel(
                user_id=user_id,  # 🔑 L'UTILISATEUR EST MAINTENANT ASSOCIÉ
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
                status="QUALIFIED"
            )
            db.add(lead)
            db.commit()

    except Exception as e:
        print(f"❌ Erreur pendant le pipeline : {e}")
        db.rollback()
    finally:
        db.close()

# ==========================================
# 10. ENDPOINTS DE L'APPLICATION (ROUTES)
# ==========================================

# --- PAGE D'ACCUEIL ---
@app.get("/")
def home(request: Request, current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user:
        return RedirectResponse(url="/login-page", status_code=status.HTTP_303_SEE_OTHER)
    
    try:
        raw_leads = db.query(LeadModel).filter(LeadModel.user_id == current_user.id).order_by(LeadModel.id.desc()).all()
        leads_dict = [lead.to_dict() for lead in raw_leads]
        
        return templates.TemplateResponse(
            request=request, 
            name="dashboard.html", 
            context={"leads": leads_dict, "user": current_user}
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- AUTHENTIFICATION ---
@app.get("/login-page")
def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")

@app.post("/register")
def register(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    db_user = db.query(UserModel).filter(UserModel.email == email).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Cet e-mail est déjà enregistré.")
    
    hashed_pwd = get_password_hash(password)
    new_user = UserModel(email=email, hashed_password=hashed_pwd)
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    return RedirectResponse(url="/login-page?registered=true", status_code=status.HTTP_303_SEE_OTHER)

@app.post("/login")
def login(response: Response, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(UserModel).filter(UserModel.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="E-mail ou mot de passe incorrect.")
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(data={"sub": user.email}, expires_delta=access_token_expires)
    
    redirect = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    redirect.set_cookie(key="access_token", value=f"Bearer {access_token}", httponly=True)
    return redirect

@app.get("/logout")
def logout():
    response = RedirectResponse(url="/login-page", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("access_token")
    return response

# --- PIPELINE & ENVOI ---
@app.post("/trigger-pipeline")
def trigger_pipeline(payload: TriggerRequest, background_tasks: BackgroundTasks, current_user: UserModel = Depends(get_current_user)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Non autorisé.")
    company = payload.target_name
    background_tasks.add_task(run_pipeline_task, current_user.id, company, payload.raw_data)
    return {"message": f"Pipeline lancé en tâche de fond pour : '{company}'"}

@app.post("/send-pending-leads")
def send_pending_leads(min_score: int = Query(7, ge=1, le=10), current_user: UserModel = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user:
        raise HTTPException(status_code=401, detail="Non autorisé.")
    if not OUTREACH_WEBHOOK_URL:
        raise HTTPException(status_code=500, detail="OUTREACH_WEBHOOK_URL non configurée.")

    try:
        pending_leads = db.query(LeadModel).filter(
            LeadModel.user_id == current_user.id,
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

# ==========================================
# 11. DÉMARRAGE DU SERVEUR
# ==========================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    print(f"🚀 Lancement d'Uvicorn sur le port {port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
