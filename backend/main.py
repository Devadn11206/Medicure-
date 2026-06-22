from fastapi import FastAPI, HTTPException, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Optional

from .ocr_service import process_document
from .db_setup import init_db
from .interactions import get_known_interactions, process_interactions
from .pharmacy import process_pharmacy_request, get_all_pharmacies
from .insurance import process_insurance_request, get_insurance_plans
from .price_alerts import (
    delete_price_alert,
    get_price_history,
    get_user_price_alerts,
    run_price_alert_check,
    start_price_alert_worker,
    subscribe_price_alert,
)
from .shortage_predictor import (
    get_demand_history,
    get_shortage_alerts,
    get_shortage_watchlist,
    predict_shortage,
    report_shortage,
    track_medicine_search,
)
from .database import get_db
from .substitution_service import check_substitutions
from sqlalchemy.orm import Session
from fastapi import Depends

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
    start_price_alert_worker()


@app.get("/")
def root():
    if FRONTEND_FILE.exists():
        return FileResponse(str(FRONTEND_FILE), media_type="text/html")
    return {"status": "ok"}


@app.post("/interactions")
def interactions_endpoint(payload: dict):
    # Minimal typing: keep it simple for this repo scaffolding.
    # Expected body: {"medicines": ["Warfarin", "Aspirin", ...]}
    medicines = payload.get("medicines", [])
    if isinstance(medicines, list):
        payload["medicines"] = [m.get("medicine_name", str(m)) if isinstance(m, dict) else str(m) for m in medicines]
    return process_interactions(payload)


@app.get("/interactions/known")
def known_interactions_endpoint():
    return {"known_interactions": get_known_interactions()}


@app.post("/pharmacy")
def pharmacy_endpoint(payload: dict):
    """
    Find best pharmacy for given medicines and location.
    Expected body: {
        "medicines": ["Augmentin 625", "Crocin 650"],
        "user_lat": 12.9716,
        "user_lng": 77.5946
    }
    """
    medicines = payload.get("medicines", [])
    if isinstance(medicines, list):
        medicines = [m.get("medicine_name", str(m)) if isinstance(m, dict) else str(m) for m in medicines]
        
    user_lat = payload.get("user_lat")
    user_lng = payload.get("user_lng")
    
    if not medicines:
        return {"error": "Medicines list is required"}
    
    return process_pharmacy_request(medicines, user_lat, user_lng)


@app.get("/pharmacy/list")
def pharmacy_list_endpoint():
    """Return all pharmacies for map display."""
    pharmacies = get_all_pharmacies()
    return {"pharmacies": pharmacies, "count": len(pharmacies)}


@app.post("/insurance")
def insurance_endpoint(payload: dict):
    result = process_insurance_request(payload)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result)
    return result


@app.get("/insurance/plans")
def insurance_plans_endpoint():
    return {"plans": get_insurance_plans(), "count": len(get_insurance_plans())}


@app.post("/alerts/subscribe")
def alerts_subscribe(payload: dict):
    result = subscribe_price_alert(payload)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result)
    return result


@app.get("/alerts/{user_id}")
def alerts_for_user(user_id: str):
    return {"alerts": get_user_price_alerts(user_id)}


@app.post("/alerts/check")
def alerts_check(payload: Optional[dict] = None):
    return run_price_alert_check(payload or {})


@app.get("/alerts/history/{medicine}")
def alerts_history(medicine: str):
    try:
        return get_price_history(medicine)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.delete("/alerts/{user_id}/{medicine}")
def alerts_delete(user_id: str, medicine: str):
    result = delete_price_alert(user_id, medicine)
    if result.get("error"):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/shortage/predict")
def shortage_predict(payload: dict):
    medicines = payload.get("medicines", [])
    if isinstance(medicines, list):
        medicines = [m.get("medicine_name", str(m)) if isinstance(m, dict) else str(m) for m in medicines]

    result = predict_shortage(medicines, payload.get("user_location"))
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/shortage/watchlist")
def shortage_watchlist():
    return get_shortage_watchlist()


@app.post("/shortage/report")
def shortage_report(payload: dict):
    result = report_shortage(
        payload.get("medicine"),
        payload.get("location"),
        payload.get("pharmacy_name"),
        payload.get("user_id", "anonymous"),
    )
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.get("/shortage/alerts")
def shortage_alerts():
    return get_shortage_alerts()


@app.get("/shortage/history/{medicine}")
def shortage_history(medicine: str):
    try:
        return get_demand_history(medicine)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/prescription/analyze")
async def analyze_prescription(file: UploadFile = File(...)):
    try:
        file_bytes = await file.read()
        result = process_document(file_bytes, file.filename)
        return result
    except ValueError as ve:
        import traceback
        traceback.print_exc()
        print(f"ValueError in analyze_prescription: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {type(e).__name__} - {str(e)}")


@app.post("/api/substitution/check")
def substitution_check(payload: dict, db: Session = Depends(get_db)):
    medicines = payload.get("medicines", [])
    if isinstance(medicines, list):
        medicines = [m.get("medicine_name", str(m)) if isinstance(m, dict) else str(m) for m in medicines]
        
    if not medicines:
        return {
            "success": True,
            "results": [],
            "monthly_savings": 0,
            "annual_savings": 0,
            "medicines_analyzed": 0,
            "alternatives_found": 0
        }
    
    return check_substitutions(medicines, db)

