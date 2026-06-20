import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .db_setup import get_db_path

SEASONAL_PATTERNS = {
    "insulin": {
        "peak_months": [6, 7, 8, 9],
        "reason": "Monsoon increases diabetic complications",
        "demand_increase_pct": 35,
    },
    "cetirizine": {
        "peak_months": [2, 3, 4, 10, 11],
        "reason": "Allergy season",
        "demand_increase_pct": 50,
    },
    "azithromycin": {
        "peak_months": [11, 12, 1, 2],
        "reason": "Respiratory infection season",
        "demand_increase_pct": 40,
    },
    "paracetamol": {
        "peak_months": [6, 7, 8, 9, 11, 12],
        "reason": "Fever/flu season",
        "demand_increase_pct": 30,
    },
    "ors": {
        "peak_months": [5, 6, 7, 8],
        "reason": "Dehydration season",
        "demand_increase_pct": 60,
    },
    "metformin": {
        "peak_months": [],
        "reason": "",
        "demand_increase_pct": 0,
    },
}

SHORTAGE_PRONE_WATCHLIST = [
    "Insulin Glargine",
    "Insulin Regular",
    "Insulin NPH",
    "Methotrexate",
    "Clonazepam",
    "Alprazolam",
    "Lithium",
    "Hydroxychloroquine",
    "Remdesivir",
    "Tocilizumab",
    "Rifampicin",
    "Isoniazid",
    "Ethambutol",
    "Adrenaline injection",
    "Atropine injection",
    "Magnesium Sulfate",
    "Oxytocin",
    "Folic Acid",
    "Phenobarbitone",
    "Digoxin",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_medicine_name(medicine: str) -> Optional[str]:
    if not medicine:
        return None
    from .price_alerts import _resolve_medicine_name
    return _resolve_medicine_name(medicine)


def _normalize_medicine(medicine: str) -> str:
    if not medicine:
        return ""
    return medicine.strip().lower()


def _get_seasonal_risk_for_medicine(medicine: str) -> int:
    normalized = _normalize_medicine(medicine)
    for key, pattern in SEASONAL_PATTERNS.items():
        if key in normalized:
            current_month = datetime.now().month
            if current_month in pattern["peak_months"]:
                return 20
            if len(pattern["peak_months"]) > 0:
                return 10
            return 0
    return 0


def _get_demand_spike_score(medicine: str) -> tuple[int, int]:
    """Returns (spike_score, spike_percentage)."""
    resolved = _resolve_medicine_name(medicine)
    if not resolved:
        return 0, 0

    with _get_db_connection() as conn:
        cur = conn.cursor()
        today = datetime.now().date()
        week_ago = today - timedelta(days=7)
        two_weeks_ago = today - timedelta(days=14)

        cur.execute(
            "SELECT SUM(search_count) FROM search_frequency WHERE LOWER(medicine)=LOWER(?) AND recorded_date >= ? AND recorded_date < ?",
            (resolved, week_ago, today),
        )
        current_week = cur.fetchone()[0] or 0

        cur.execute(
            "SELECT SUM(search_count) FROM search_frequency WHERE LOWER(medicine)=LOWER(?) AND recorded_date >= ? AND recorded_date < ?",
            (resolved, two_weeks_ago, week_ago),
        )
        previous_week = cur.fetchone()[0] or 0

    if previous_week == 0:
        spike_pct = 0
    else:
        spike_pct = int(((current_week - previous_week) / previous_week) * 100)

    if spike_pct >= 60:
        return 30, spike_pct
    elif spike_pct >= 30:
        return 15, spike_pct
    else:
        return 0, spike_pct


def _get_shortage_report_score(medicine: str, location: Optional[str] = None) -> tuple[int, int]:
    """Returns (report_score, report_count)."""
    resolved = _resolve_medicine_name(medicine)
    if not resolved:
        return 0, 0

    with _get_db_connection() as conn:
        cur = conn.cursor()
        two_weeks_ago = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()

        if location:
            cur.execute(
                "SELECT COUNT(*) FROM shortage_reports WHERE LOWER(medicine)=LOWER(?) AND LOWER(location)=LOWER(?) AND confirmed=1 AND reported_at >= ?",
                (resolved, location, two_weeks_ago),
            )
        else:
            cur.execute(
                "SELECT COUNT(*) FROM shortage_reports WHERE LOWER(medicine)=LOWER(?) AND confirmed=1 AND reported_at >= ?",
                (resolved, two_weeks_ago),
            )
        count = cur.fetchone()[0] or 0

    if count >= 6:
        return 35, count
    elif count >= 3:
        return 25, count
    elif count >= 1:
        return 15, count
    else:
        return 0, count


def _get_pharmacy_stock_score(medicine: str) -> int:
    """Check if medicine is in stock at pharmacies."""
    resolved = _resolve_medicine_name(medicine)
    if not resolved:
        return 0

    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM pharmacy_inventory WHERE LOWER(medicine_name)=LOWER(?) AND in_stock=1",
            (resolved,),
        )
        in_stock = cur.fetchone()[0] or 0

        cur.execute(
            "SELECT COUNT(*) FROM pharmacy_inventory WHERE LOWER(medicine_name)=LOWER(?)",
            (resolved,),
        )
        total = cur.fetchone()[0] or 0

    if total == 0:
        return 0
    if in_stock == 0:
        return 20
    if (in_stock / total) < 0.5:
        return 10
    return 0


def _get_alternatives(medicine: str) -> List[str]:
    """Get therapeutic alternatives for a medicine."""
    try:
        from .interactions import get_known_interactions
        interactions = get_known_interactions()
        alternatives = set()
        for interaction in interactions:
            if interaction.get("drug_a", "").lower() == medicine.lower():
                alternatives.add(interaction.get("drug_b", ""))
            elif interaction.get("drug_b", "").lower() == medicine.lower():
                alternatives.add(interaction.get("drug_a", ""))
        return list(alternatives)[:3]
    except Exception:
        return []


def _medicine_exists_in_db(medicine: str) -> bool:
    """Return True if the medicine can be resolved to a pharmacy inventory row."""
    resolved = _resolve_medicine_name(medicine)
    if not resolved:
        return False
    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM pharmacy_inventory WHERE LOWER(medicine_name)=LOWER(?) LIMIT 1",
            (resolved,),
        )
        return cur.fetchone() is not None


def _get_seasonal_meta_for_medicine(medicine: str) -> Tuple[int, int, str]:
    """
    Returns (seasonal_score, seasonal_flag, seasonal_reason_for_prompt)
    seasonal_flag is 1 if current month is in peak months for any matched key.
    """
    normalized = _normalize_medicine(medicine)
    current_month = datetime.now().month

    for key, pattern in SEASONAL_PATTERNS.items():
        if key in normalized:
            peak_months = pattern.get("peak_months") or []
            in_peak = current_month in peak_months if peak_months else False
            if in_peak:
                return 20, 1, pattern.get("reason", "") or ""
            if len(peak_months) > 0:
                return 10, 0, pattern.get("reason", "") or ""
            return 0, 0, pattern.get("reason", "") or ""

    return 0, 0, ""


def _try_generate_reason_bullets_with_gemini(
    medicine: str,
    score: int,
    demand_spike_pct: int,
    report_count: int,
    seasonal_peak: int,
) -> Optional[List[str]]:
    """
    Gemini ONLY for human-readable bullet reasons.
    Graceful fallback: if library/key isn't available, return None.
    """
    # Avoid hard dependency; repo might not have gemini SDK configured.
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return None

    try:
        import google.generativeai as genai  # type: ignore
    except Exception:
        return None

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-1.5-flash")

        prompt = (
            "You are a medical assistant helping predict medicine shortages in India. "
            "Return ONLY a JSON array of 2-3 short bullet point strings. "
            "A medicine called {medicine} has a shortage risk score of {score}/100 based on: "
            "demand spike={spike}%, shortage reports={reports}, seasonal peak={seasonal}. "
            "Write 2-3 short bullet points explaining why this medicine may face shortage in India right now. "
            "Be specific and practical. Return only a JSON array of strings."
        ).format(
            medicine=medicine,
            score=score,
            spike=demand_spike_pct,
            reports=report_count,
            seasonal=seasonal_peak,
        )

        resp = model.generate_content(prompt)
        text = getattr(resp, "text", None) or str(resp)

        bullets = json.loads(text)
        if isinstance(bullets, list) and all(isinstance(x, str) for x in bullets):
            return bullets[:3]
        return None
    except Exception:
        return None


def track_medicine_search(medicine: str, location: Optional[str] = None) -> None:
    """Increment search frequency for a medicine (called passively by other endpoints)."""
    resolved = _resolve_medicine_name(medicine)
    if not resolved:
        return

    with _get_db_connection() as conn:
        cur = conn.cursor()
        today = datetime.now().date()
        location_val = location or "unknown"

        cur.execute(
            "SELECT 1 FROM search_frequency WHERE LOWER(medicine)=LOWER(?) AND recorded_date=?",
            (resolved, today),
        )
        if cur.fetchone():
            cur.execute(
                "UPDATE search_frequency SET search_count = search_count + 1 WHERE LOWER(medicine)=LOWER(?) AND recorded_date=?",
                (resolved, today),
            )
        else:
            cur.execute(
                "INSERT INTO search_frequency (medicine, location, search_count, recorded_date) VALUES (?, ?, 1, ?)",
                (resolved, location_val, today),
            )
        conn.commit()


def report_shortage(medicine: str, location: str, pharmacy_name: str, user_id: str) -> Dict[str, Any]:
    """User reports a shortage of a medicine at a pharmacy."""
    resolved = _resolve_medicine_name(medicine)
    if not resolved:
        return {"error": "Medicine not found in database"}

    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM shortage_reports WHERE LOWER(medicine)=LOWER(?) AND LOWER(location)=LOWER(?) AND reported_by=? AND DATE(reported_at)=DATE('now')",
            (resolved, location, user_id),
        )
        if cur.fetchone():
            return {"status": "duplicate", "message": "Report already received today for this medicine and location"}

        cur.execute(
            "INSERT INTO shortage_reports (medicine, location, reported_by, pharmacy_name, confirmed, reported_at) VALUES (?, ?, ?, ?, 1, ?)",
            (resolved, location, user_id, pharmacy_name, _now_iso()),
        )
        conn.commit()

    return {"status": "reported", "medicine": resolved, "location": location}


def predict_shortage(medicines: List[str], user_location: Optional[str] = None) -> Dict[str, Any]:
    """Predict shortage risk for a list of medicines."""
    if not medicines:
        return {"error": "Medicines list is required"}

    predictions = []
    for medicine in medicines:
        # Resolve and validate existence
        resolved = _resolve_medicine_name(medicine)
        if not resolved or not _medicine_exists_in_db(medicine):
            predictions.append(
                {
                    "medicine": medicine,
                    "shortage_risk": "UNKNOWN",
                    "risk_score": 0,
                    "predicted_shortage_in_days": None,
                    "reason": [],
                    "recommendation": "No data available for this medicine",
                    "days_to_stock": 0,
                    "alternatives": [],
                }
            )
            continue

        demand_score, demand_pct = _get_demand_spike_score(medicine)
        report_score, report_count = _get_shortage_report_score(medicine, user_location)
        seasonal_score, seasonal_flag, seasonal_reason = _get_seasonal_meta_for_medicine(medicine)
        stock_score = _get_pharmacy_stock_score(medicine)

        total_score = demand_score + report_score + seasonal_score + stock_score
        total_score = min(100, max(0, total_score))

        if total_score >= 61:
            risk_level = "HIGH"
            days_estimate = 5
            recommendation = (
                "Critical: Stock up 30-day supply immediately"
                if "insulin" in resolved.lower() or "metformin" in resolved.lower()
                else "Buy now — shortage expected within a week"
            )
            days_to_stock = 30 if "insulin" in resolved.lower() else 7
        elif total_score >= 31:
            risk_level = "MODERATE"
            days_estimate = 14
            recommendation = "Consider buying 2-week supply as precaution"
            days_to_stock = 14
        else:
            risk_level = "LOW"
            days_estimate = None
            recommendation = "No action needed. Monitor for changes."
            days_to_stock = 0

        # Gemini ONLY for reason bullets; fallback: omit (empty list)
        reason_bullets = _try_generate_reason_bullets_with_gemini(
            medicine=resolved,
            score=total_score,
            demand_spike_pct=demand_pct,
            report_count=report_count,
            seasonal_peak=seasonal_flag,
        )
        if reason_bullets is None:
            reason_bullets = []

        alternatives = _get_alternatives(medicine)

        predictions.append(
            {
                "medicine": resolved,
                "shortage_risk": risk_level,
                "risk_score": total_score,
                "predicted_shortage_in_days": days_estimate,
                "reason": reason_bullets[:3],
                "recommendation": recommendation,
                "days_to_stock": days_to_stock,
                "alternatives": alternatives,
            }
        )

    high_risk = sum(1 for p in predictions if p["shortage_risk"] == "HIGH")
    moderate_risk = sum(1 for p in predictions if p["shortage_risk"] == "MODERATE")
    low_risk = sum(1 for p in predictions if p["shortage_risk"] == "LOW")

    return {
        "predictions": predictions,
        "summary": {
            "high_risk_count": high_risk,
            "moderate_risk_count": moderate_risk,
            "low_risk_count": low_risk,
            "action_required": (high_risk > 0 or moderate_risk > 0),
        },
    }


def get_shortage_watchlist() -> Dict[str, Any]:
    """Get the public shortage watchlist with current risk levels."""
    with _get_db_connection() as conn:
        cur = conn.cursor()
        watchlist = []
        for medicine in SHORTAGE_PRONE_WATCHLIST:
            demand_score, _ = _get_demand_spike_score(medicine)
            report_score, _ = _get_shortage_report_score(medicine)
            seasonal_score, seasonal_flag, _ = _get_seasonal_meta_for_medicine(medicine)
            stock_score = _get_pharmacy_stock_score(medicine)

            total_score = demand_score + report_score + seasonal_score + stock_score
            total_score = min(100, max(0, total_score))

            if total_score >= 61:
                risk = "HIGH"
            elif total_score >= 31:
                risk = "MODERATE"
            else:
                risk = "LOW"

            watchlist.append(
                {
                    "medicine": medicine,
                    "risk_level": risk,
                    "risk_score": total_score,
                    "seasonal_flag": seasonal_flag,
                    "last_updated": _now_iso(),
                }
            )

        return {"watchlist": watchlist, "count": len(watchlist)}


def get_shortage_alerts() -> Dict[str, Any]:
    """Get all active HIGH risk shortage alerts."""
    predictions = predict_shortage(SHORTAGE_PRONE_WATCHLIST)
    high_risk_alerts = [
        p for p in predictions["predictions"] if p["shortage_risk"] == "HIGH"
    ]
    return {"alerts": high_risk_alerts, "count": len(high_risk_alerts)}


def get_demand_history(medicine: str) -> Dict[str, Any]:
    """Get demand trend for a medicine (search frequency over time)."""
    resolved = _resolve_medicine_name(medicine)
    if not resolved:
        raise ValueError("Medicine not found")

    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT recorded_date, SUM(search_count) as total_searches FROM search_frequency WHERE LOWER(medicine)=LOWER(?) GROUP BY recorded_date ORDER BY recorded_date ASC LIMIT 90",
            (resolved,),
        )
        history = [
            {"date": row[0], "searches": row[1]} for row in cur.fetchall()
        ]

    if not history:
        raise ValueError("No demand history available")

    return {"medicine": resolved, "history": history}
