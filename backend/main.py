from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path

from .db_setup import init_db
from .interactions import get_known_interactions, process_interactions

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_FILE = BASE_DIR / "mediwise-ai (1).html"

app = FastAPI(title="MediWise AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    init_db()


@app.get("/")
def root():
    if FRONTEND_FILE.exists():
        return FileResponse(str(FRONTEND_FILE), media_type="text/html")
    return {"status": "ok"}


@app.post("/interactions")
def interactions_endpoint(payload: dict):
    # Minimal typing: keep it simple for this repo scaffolding.
    # Expected body: {"medicines": ["Warfarin", "Aspirin", ...]}
    return process_interactions(payload)


@app.get("/interactions/known")
def known_interactions_endpoint():
    return {"known_interactions": get_known_interactions()}


