# main.py
import os
from fastapi import FastAPI, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from clients.dedall_energy.pipeline import run_dedall_pipeline
from core.database import SessionLocal, LeadModel, init_db
from integrations.email_outreach import OutreachConnector

init_db()

app = FastAPI(title="AI Sales Engine API")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/")
def read_root():
    return {"status": "ok", "message": "AI Sales Engine est en ligne !"}

@app.post("/trigger-pipeline")
def trigger_pipeline(query: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_dedall_pipeline, query)
    return {"status": "started", "message": f"Pipeline lancé pour '{query}'"}

@app.get("/leads")
def get_leads(limit: int = 50, db: Session = Depends(get_db)):
    leads = db.query(LeadModel).order_by(LeadModel.priority_score.desc()).limit(limit).all()
    return {"count": len(leads), "leads": leads}

# 🚀 NOUVELLE ROUTE : Envoi des leads en BDD vers l'outil de prospection
@app.post("/send-pending-leads")
def send_pending_leads(min_score: int = 7, db: Session = Depends(get_db)):
    """
    Récupère les leads qualifiés en BDD et les envoie vers la campagne d'emailing.
    """
    pending_leads = db.query(LeadModel).filter(
        LeadModel.status == "QUALIFIED",
        LeadModel.priority_score >= min_score
    ).all()

    if not pending_leads:
        return {"status": "info", "message": "Aucun lead en attente d'envoi."}

    outreach = OutreachConnector()
    sent_count = 0

    for lead in pending_leads:
        payload = {
            "id": lead.id,
            "company_name": lead.company_name,
            "website": lead.website,
            "phone": lead.phone,
            "email_subject": lead.email_subject,
            "email_body": lead.email_body,
            "priority_score": lead.priority_score
        }

        success = outreach.send_lead_to_campaign(payload)
        if success:
            lead.status = "SENT"
            sent_count += 1

    db.commit()

    return {
        "status": "success",
        "sent_count": sent_count,
        "message": f"{sent_count} leads envoyés avec succès dans la campagne !"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
