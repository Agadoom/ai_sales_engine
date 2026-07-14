import os
import time
import json
import secrets
import requests
import uvicorn
from datetime import datetime, timedelta
from typing import List, Optional, Literal

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query, Request, Depends, status, Form, Response
from fastapi.responses import RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials, OAuth2PasswordBearer
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime, ForeignKey, text
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from openai import OpenAI
import jwt
import bcrypt

# ==========================================
# 1. INITIALISATION DE FASTAPI & ENV
# ==========================================

app = FastAPI(
    title="Dedall Energy - Automated Outreach Engine",
    version="2.0.0"
)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "DedallEnergy2026!")
HUNTER_API_KEY = os.getenv("HUNTER_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
HEYGEN_API_KEY = os.getenv("HEYGEN_API_KEY")
OUTREACH_WEBHOOK_URL = os.getenv("OUTREACH_WEBHOOK_URL")

if not DATABASE_URL:
    raise ValueError("⚠️ DATABASE_URL est manquante dans les variables d'environnement.")

client_openai = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ==========================================
# 2. CONFIGURATION BASE DE DONNÉES
# ==========================================

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================================
# 3. MODÈLES DATABASE (SQLAlchemy)
# ==========================================

class UserModel(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    subscription_status = Column(String, default="trial")
    stripe_customer_id = Column(String, nullable=True)
    
    openai_api_key = Column(String, nullable=True)
    hunter_api_key = Column(String, nullable=True)
    heygen_api_key = Column(String, nullable=True)

    leads = relationship("LeadModel", back_populates="owner", cascade="all, delete-orphan")


class LeadModel(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    
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

with engine.connect() as conn:
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS manager_name VARCHAR;"))
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS phone VARCHAR;"))
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS email VARCHAR;"))
    conn.execute(text("ALTER TABLE leads ADD COLUMN IF NOT EXISTS address VARCHAR;"))
    conn.commit()

# ==========================================
# 4. SÉCURITÉ & AUTHENTIFICATION (BCRYPT + JWT)
# ==========================================

SECRET_KEY = os.getenv("JWT_SECRET", "CHANGE_MOI_AVEC_UNE_CLE_TRES_SECRETE_123456")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="login", auto_error=False)

def get_password_hash(password: str) -> str:
    pwd_bytes = password.encode('utf-8')[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    pwd_bytes = plain_password.encode('utf-8')[:72]
    return bcrypt.checkpw(pwd_bytes, hashed_password.encode('utf-8'))

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
# 5. TEMPLATES JINJA2
# ==========================================

templates = Jinja2Templates(directory="templates")

def escapejs_filter(val):
    if val is None:
        return ""
    return json.dumps(str(val))[1:-1]

templates.env.filters["escapejs"] = escapejs_filter

# ==========================================
# 6. PYDANTIC MODELS
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
# 7. PIPELINE & FONCTIONS AUXILIAIRES
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
            return res.json().get("data", {}).get("email")
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

def qualify_and_generate(company_name: str, manager_name: str, raw_data: str):
    if not client_openai:
        raise ValueError("OPENAI_API_KEY n'est pas définie dans l'environnement.")

    system_qualif = "Tu es un expert en qualification B2B pour courtier en énergie."
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

Termine TOUJOURS l'email exactement par cette signature :
<p style="margin-top: 24px; margin-bottom: 0;">Cordialement,</p>
<p style="font-weight: bold; color: #1e3a8a; margin: 0;">Benoît</p>
<p style="font-size: 12px; color: #666666; margin: 0;">Expert Solutions Énergie — Dedall Energy</p>
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

def run_pipeline_task(query: str, raw_data: str, user_id: Optional[int] = None):
    db = SessionLocal()
    try:
        prospects = fetch_company_legal_info(query, limit=5)
        for p in prospects:
            company_name = p["company_name"]
            manager_name = p["manager_name"]
            manager_email = p.get("email")

            qualif, email_info = qualify_and_generate(company_name, manager_name, raw_data)

            lead = LeadModel(
                user_id=user_id,
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
# 8. ENDPOINTS / ROUTES FASTAPI
# ==========================================

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
def login(email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
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

@app.get("/")
def home(request: Request, current_user: Optional[UserModel] = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user:
        return RedirectResponse(url="/login-page", status_code=status.HTTP_303_SEE_OTHER)
    
    raw_leads = db.query(LeadModel).filter(LeadModel.user_id == current_user.id).order_by(LeadModel.id.desc()).all()
    leads_dict = [lead.to_dict() for lead in raw_leads]
    
    return templates.TemplateResponse(
        request=request, 
        name="dashboard.html", 
        context={"leads": leads_dict, "user": current_user}
    )

@app.post("/trigger-pipeline")
def trigger_pipeline(payload: TriggerRequest, background_tasks: BackgroundTasks, current_user: Optional[UserModel] = Depends(get_current_user)):
    company = payload.target_name
    user_id = current_user.id if current_user else None
    background_tasks.add_task(run_pipeline_task, company, payload.raw_data, user_id)
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
# 9. SERVEUR
# ==========================================

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8080))
    print(f"🚀 Lancement d'Uvicorn sur le port {port}...")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
