# main.py
import os
from fastapi import FastAPI, BackgroundTasks, Depends
from sqlalchemy.orm import Session
from clients.dedall_energy.pipeline import run_dedall_pipeline
from core.database import SessionLocal, LeadModel, init_db

# Initialise les tables au démarrage
init_db()

app = FastAPI(title="AI Sales Engine API")

# Dependency pour la BDD
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/")
def read_root():
    return {"status": "ok", "message": "AI Sales Engine + PostgreSQL est en ligne !"}

@app.post("/trigger-pipeline")
def trigger_pipeline(query: str, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_dedall_pipeline, query)
    return {"status": "started", "message": f"Pipeline lancé pour '{query}'"}

@app.get("/leads")
def get_leads(limit: int = 50, db: Session = Depends(get_db)):
    """ Récupère les leads enregistrés en BDD """
    leads = db.query(LeadModel).order_by(LeadModel.priority_score.desc()).limit(limit).all()
    return {"count": len(leads), "leads": leads}

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
