# main.py
import os
from fastapi import FastAPI, BackgroundTasks
from clients.dedall_energy.pipeline import run_dedall_pipeline

app = FastAPI(title="AI Sales Engine API")

@app.get("/")
def read_root():
    # Route pour vérifier que le serveur est bien en ligne
    return {"status": "ok", "message": "AI Sales Engine est en ligne !"}

@app.post("/trigger-pipeline")
def trigger_pipeline(query: str, background_tasks: BackgroundTasks):
    """
    Déclenche le pipeline en arrière-plan pour ne pas bloquer la réponse HTTP.
    Exemple de query : 'Boulangerie Paris'
    """
    background_tasks.add_task(run_dedall_pipeline, query)
    return {
        "status": "started",
        "message": f"Pipeline lancé en arrière-plan pour la recherche : '{query}'"
    }

if __name__ == "__main__":
    import uvicorn
    # Railway injecte automatiquement la variable PORT dans l'environnement
    port = int(os.getenv("PORT", 8080))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
