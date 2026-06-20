from fastapi import FastAPI

from .db_setup import init_db
from .interactions import get_known_interactions, process_interactions

app = FastAPI(title="MediWise AI")


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/")
def root():
    return {"status": "ok"}


@app.post("/interactions")
def interactions_endpoint(payload: dict):
    # Minimal typing: keep it simple for this repo scaffolding.
    # Expected body: {"medicines": ["Warfarin", "Aspirin", ...]}
    return process_interactions(payload)


@app.get("/interactions/known")
def known_interactions_endpoint():
    return {"known_interactions": get_known_interactions()}


