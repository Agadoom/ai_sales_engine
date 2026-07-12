# core/database.py
import os
from sqlalchemy import create_engine, Column, Integer, String, Boolean, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime

# Railway fournit DATABASE_URL. En local, on utilise SQLite par défaut.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./local_leads.db")

# Correctif pour la compatibilité SQLAlchemy avec les anciennes URLs "postgres://"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class LeadModel(Base):
    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    company_name = Column(String, index=True)
    address = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    website = Column(String, nullable=True)
    
    # Données IA & Qualification
    energy_intensity = Column(String, nullable=True)
    priority_score = Column(Integer, default=0)
    personalized_hook = Column(Text, nullable=True)
    should_contact = Column(Boolean, default=False)
    
    # Email généré
    email_subject = Column(String, nullable=True)
    email_body = Column(Text, nullable=True)
    
    # Suivi du pipeline
    status = Column(String, default="QUALIFIED") # STATUTS: QUALIFIED, SENT, ERROR
    created_at = Column(DateTime, default=datetime.utcnow)

def init_db():
    """Crée les tables dans la BDD si elles n'existent pas"""
    Base.metadata.create_all(bind=engine)  # ✅ La bonne méthode

