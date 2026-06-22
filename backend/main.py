from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Optional

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

from .ocr import ocr_from_upload, ocr_from_raw_text, get_ocr_history
from .bill_auditor import (
    audit_from_bill_upload,
    audit_from_manual_items,
    get_audit_history,
    get_audit_report,
    get_dispute_letter,
)

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
    result = predict_shortage(payload.get("medicines", []), payload.get("user_location"))
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


@app.post("/ocr")
async def ocr_endpoint(file, user_id: Optional[str] = None):
    """
    Multipart upload:
      - file: prescription image (jpg/png/pdf)
      - user_id: string (optional)
    """
    return await ocr_from_upload(file, user_id)


@app.post("/ocr/text")
async def ocr_text_endpoint(payload: dict):
    """
    Testing without image:
      { "raw_text": "Augmentin 625 1-0-1 x 5 days\nCrocin 650 SOS" }
    """
    raw_text = payload.get("raw_text", "")
    user_id = payload.get("user_id")
    return await ocr_from_raw_text(raw_text=raw_text, user_id=user_id)


@app.get("/ocr/history/{user_id}")
def ocr_history_endpoint(user_id: str):
    return {"history": get_ocr_history(user_id)}


@app.post("/audit")
async def audit_endpoint(
    file,  # multipart file
    bill_type: Optional[str] = None,
    user_id: Optional[str] = None,
):
    """
    Multipart upload:
      - file: jpg/png/pdf (<= 10MB)
      - bill_type: optional string
      - user_id: optional string
    """
    if file is None:
        raise HTTPException(status_code=400, detail="file is required")

    bill_type = bill_type or "general"
    filename = getattr(file, "filename", None) or "upload"
    try:
        content = await file.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Unable to read upload: {exc}")

    try:
        return audit_from_bill_upload(
            file_bytes=content,
            filename=filename,
            bill_type=bill_type,
            user_id=user_id,
            include_dispute_letter=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        # Gemini / extraction failure -> spec: 500
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/audit/manual")
def audit_manual_endpoint(payload: dict):
    """
    Manual JSON audit input:
      {
        "items": [ {..extracted/line-item-ish..}, ... ],
        "bill_type": "general" (optional),
        "user_id": "abc" (optional),
        "include_dispute_letter": true/false (optional)
      }
    """
    items = payload.get("items") or []
    bill_type = payload.get("bill_type") or "general"
    user_id = payload.get("user_id")
    include_dispute_letter = bool(payload.get("include_dispute_letter", True))

    try:
        return audit_from_manual_items(
            items=items,
            bill_type=bill_type,
            user_id=user_id,
            include_dispute_letter=include_dispute_letter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/audit/{audit_id}")
def audit_get_endpoint(audit_id: str):
    try:
        return get_audit_report(audit_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/audit/{audit_id}/letter")
def audit_letter_endpoint(audit_id: str):
    try:
        return {"audit_id": audit_id, "letter": get_dispute_letter(audit_id)}
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/audit/history/{user_id}")
def audit_history_endpoint(user_id: str):
    return {"history": get_audit_history(user_id)}
