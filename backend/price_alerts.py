import random
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .db_setup import get_db_path

CHECK_INTERVAL_SECONDS = 24 * 60 * 60  # 24 hours

PRICE_DROP_THRESHOLD = 5.0
PRICE_SPIKE_THRESHOLD = 15.0

ALERT_TYPE_PRICE_DROP = "PRICE_DROP"
ALERT_TYPE_TARGET_REACHED = "TARGET_REACHED"
ALERT_TYPE_PRICE_SPIKE = "PRICE_SPIKE"
ALERT_TYPE_LOWEST_30D = "LOWEST_30D"

SOURCE_MANUAL = "manual"
SOURCE_SCRAPE = "scrape"
SOURCE_USER_REPORT = "user_report"
SOURCE_SIMULATED = "simulated"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(), timeout=60)
    conn.row_factory = sqlite3.Row
    return conn


def _normalize_medicine(medicine: str) -> str:
    if not medicine:
        return ""
    normalized = medicine.strip().lower()
    normalized = normalized.replace('mg', '').replace('ml', '').replace('tablet', '').replace('tab', '').replace('capsule', '').replace('caps', '')
    normalized = normalized.replace('  ', ' ').strip()
    return normalized


def _find_similar_medicines(term: str) -> List[str]:
    normalized = _normalize_medicine(term)
    pattern = f"%{normalized}%"
    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT medicine_name FROM pharmacy_inventory WHERE LOWER(medicine_name) LIKE ? OR LOWER(medicine_name) LIKE ? LIMIT 5",
            (pattern, f"%{term.lower()}%"),
        )
        return [row[0] for row in cur.fetchall()]


def _resolve_medicine_name(medicine: str) -> Optional[str]:
    normalized = _normalize_medicine(medicine)
    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT medicine_name FROM pharmacy_inventory WHERE LOWER(medicine_name)=LOWER(?) LIMIT 1",
            (medicine.strip(),),
        )
        row = cur.fetchone()
        if row:
            return row[0]

        cur.execute(
            "SELECT DISTINCT medicine_name FROM pharmacy_inventory WHERE LOWER(medicine_name)=? LIMIT 1",
            (normalized,),
        )
        row = cur.fetchone()
        if row:
            return row[0]

        cur.execute(
            "SELECT DISTINCT medicine_name FROM pharmacy_inventory WHERE LOWER(medicine_name) LIKE ? LIMIT 1",
            (f"%{normalized}%",),
        )
        row = cur.fetchone()
        if row:
            return row[0]

    return None


def _get_latest_db_price(medicine: str) -> Optional[float]:
    resolved = _resolve_medicine_name(medicine)
    if not resolved:
        return None
    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT MIN(price_inr) AS price FROM pharmacy_inventory WHERE LOWER(medicine_name)=LOWER(?) AND in_stock=1",
            (resolved,),
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return float(row[0])
        cur.execute(
            "SELECT MIN(price_inr) AS price FROM pharmacy_inventory WHERE LOWER(medicine_name)=LOWER(?)",
            (resolved,),
        )
        row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else None
    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT MIN(price_inr) AS price FROM pharmacy_inventory WHERE LOWER(medicine_name)=LOWER(?) AND in_stock=1",
            (medicine,),
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return float(row[0])
        cur.execute(
            "SELECT MIN(price_inr) AS price FROM pharmacy_inventory WHERE LOWER(medicine_name)=LOWER(?)",
            (medicine,),
        )
        row = cur.fetchone()
        return float(row[0]) if row and row[0] is not None else None


def _insert_price_history(medicine: str, price: float, source: str = SOURCE_SCRAPE) -> None:
    with _get_db_connection() as conn:
        conn.execute(
            "INSERT INTO price_history (medicine, price, recorded_at, source) VALUES (?, ?, ?, ?)",
            (medicine, price, _now_iso(), source),
        )
        conn.commit()


def _update_price_alert_current_price(user_id: str, medicine: str, new_price: float) -> None:
    with _get_db_connection() as conn:
        conn.execute(
            "UPDATE price_alerts SET current_price = ?, updated_at = ? WHERE user_id = ? AND LOWER(medicine) = LOWER(?)",
            (new_price, _now_iso(), user_id, medicine),
        )
        conn.commit()


def _insert_alert_log(
    user_id: str,
    medicine: str,
    alert_type: str,
    old_price: float,
    new_price: float,
    was_seen: int = 0,
) -> None:
    with _get_db_connection() as conn:
        conn.execute(
            "INSERT INTO alert_log (user_id, medicine, alert_type, old_price, new_price, triggered_at, was_seen) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, medicine, alert_type, old_price, new_price, _now_iso(), was_seen),
        )
        conn.commit()


def _query_price_history(medicine: str, days: int = 30) -> List[Dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT price, recorded_at FROM price_history WHERE LOWER(medicine)=LOWER(?) AND recorded_at >= ? ORDER BY recorded_at ASC",
            (medicine, cutoff.isoformat()),
        )
        return [dict(price=row[0], recorded_at=row[1]) for row in cur.fetchall()]


def _trend_analysis(medicine: str) -> Dict[str, Any]:
    history_30d = _query_price_history(medicine, days=30)
    if len(history_30d) < 2:
        return {
            "trend": "INSUFFICIENT_DATA",
            "seven_day_average": None,
            "thirty_day_average": None,
            "lowest_price_30d": None,
            "highest_price_30d": None,
            "best_time_to_buy": "INSUFFICIENT_DATA",
            "latest_price": None,
        }

    prices = [entry["price"] for entry in history_30d]
    now = datetime.now(timezone.utc)
    cutoff_7d = now - timedelta(days=7)

    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT price FROM price_history WHERE LOWER(medicine)=LOWER(?) AND recorded_at >= ? ORDER BY recorded_at ASC",
            (medicine, cutoff_7d.isoformat()),
        )
        prices_7d = [row[0] for row in cur.fetchall()]

    if not prices_7d:
        prices_7d = prices

    avg_7d = sum(prices_7d) / len(prices_7d)
    avg_30d = sum(prices) / len(prices)
    low_30d = min(prices)
    high_30d = max(prices)
    latest_price = prices[-1]

    if latest_price <= low_30d:
        best_time = "Now — lowest price in 30 days"
        trend = "FALLING"
    elif avg_7d < avg_30d * 0.98:
        best_time = "Prices are dropping. Wait 3-5 days for better price."
        trend = "FALLING"
    elif avg_7d > avg_30d * 1.02:
        best_time = "Prices are rising. Buy now before further increase."
        trend = "RISING"
    else:
        best_time = "Price is stable. Safe to buy anytime."
        trend = "STABLE"

    return {
        "trend": trend,
        "seven_day_average": round(avg_7d, 2),
        "thirty_day_average": round(avg_30d, 2),
        "lowest_price_30d": round(low_30d, 2),
        "highest_price_30d": round(high_30d, 2),
        "best_time_to_buy": best_time,
        "latest_price": round(latest_price, 2),
    }


def subscribe_price_alert(payload: Dict[str, Any]) -> Dict[str, Any]:
    user_id = str(payload.get("user_id", "")).strip()
    medicine = _normalize_medicine(payload.get("medicine", ""))
    target_price = payload.get("target_price")
    current_price = payload.get("current_price")
    notify_on_any_drop = bool(payload.get("notify_on_any_drop", False))

    if not user_id or not medicine:
        return {"error": "user_id and medicine are required"}

    if current_price is None or target_price is None:
        return {"error": "current_price and target_price are required"}

    try:
        current_price = float(current_price)
        target_price = float(target_price)
    except (TypeError, ValueError):
        return {"error": "current_price and target_price must be numeric"}

    if current_price <= 0 or target_price <= 0:
        return {"error": "Prices must be greater than 0"}

    if target_price >= current_price:
        return {"error": "Target must be lower than the current price"}

    latest_price = _get_latest_db_price(medicine)
    if latest_price is None:
        suggestions = _find_similar_medicines(medicine)
        return {
            "error": "Medicine not found in price database",
            "suggestions": suggestions,
        }

    resolved_medicine = _resolve_medicine_name(medicine) or medicine
    created_at = _now_iso()
    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT created_at FROM price_alerts WHERE user_id = ? AND LOWER(medicine)=LOWER(?)",
            (user_id, resolved_medicine),
        )
        row = cur.fetchone()
        if row:
            created_at = row[0]
        cur.execute(
            "INSERT OR REPLACE INTO price_alerts (user_id, medicine, target_price, current_price, notify_on_any_drop, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (user_id, resolved_medicine, target_price, current_price, int(notify_on_any_drop), created_at, _now_iso()),
        )
        conn.commit()

    _insert_price_history(medicine, current_price, SOURCE_USER_REPORT)
    trend = _trend_analysis(medicine)

    return {
        "status": "subscribed",
        "user_id": user_id,
        "medicine": medicine,
        "current_price": current_price,
        "target_price": target_price,
        "notify_on_any_drop": notify_on_any_drop,
        "trend": trend,
        "message": f"Price alert saved for {medicine}. Current baseline price is ₹{current_price}.",
    }


def get_user_price_alerts(user_id: str) -> List[Dict[str, Any]]:
    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, medicine, target_price, current_price, notify_on_any_drop, is_active, created_at FROM price_alerts WHERE user_id = ? AND is_active = 1",
            (user_id,),
        )
        alerts = [
            {
                "user_id": row[0],
                "medicine": row[1],
                "target_price": row[2],
                "current_price": row[3],
                "notify_on_any_drop": bool(row[4]),
                "is_active": bool(row[5]),
                "created_at": row[6],
            }
            for row in cur.fetchall()
        ]
    return alerts


def get_price_history(medicine: str) -> Dict[str, Any]:
    history = _query_price_history(medicine, days=365)
    if not history:
        raise ValueError("Medicine history not found")
    return {"medicine": medicine, "history": history}


def delete_price_alert(user_id: str, medicine: str) -> Dict[str, Any]:
    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE price_alerts SET is_active = 0 WHERE user_id = ? AND LOWER(medicine)=LOWER(?)",
            (user_id, medicine),
        )
        if cur.rowcount == 0:
            return {"error": "Alert not found"}
        conn.commit()
    return {"status": "cancelled", "user_id": user_id, "medicine": medicine}


def _update_inventory_prices_for_medicine(medicine: str, new_price: float) -> None:
    with _get_db_connection() as conn:
        conn.execute(
            "UPDATE pharmacy_inventory SET price_inr = ? WHERE LOWER(medicine_name)=LOWER(?)",
            (round(new_price), medicine),
        )
        conn.commit()


def _simulate_price_updates() -> List[Dict[str, Any]]:
    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT medicine FROM price_alerts WHERE is_active = 1"
        )
        medicines = [row[0] for row in cur.fetchall()]

    simulated = []
    for medicine in medicines:
        latest_price = _get_latest_db_price(medicine)
        if latest_price is None:
            continue
        factor = random.uniform(0.85, 1.10)
        new_price = max(1.0, round(latest_price * factor, 2))
        _update_inventory_prices_for_medicine(medicine, new_price)
        simulated.append({"medicine": medicine, "new_price": new_price, "factor": round(factor, 3)})
    return simulated


def _process_price_alert(alert: Dict[str, Any], new_price: float) -> Optional[Dict[str, Any]]:
    old_price = float(alert["current_price"])
    target_price = float(alert["target_price"])
    notify_any = bool(alert["notify_on_any_drop"])
    medicine = alert["medicine"]
    user_id = alert["user_id"]

    alert_payload: Optional[Dict[str, Any]] = None

    if new_price is None:
        return None

    if new_price <= target_price:
        alert_payload = ALERT_TYPE_TARGET_REACHED
    elif new_price > old_price * (1 + PRICE_SPIKE_THRESHOLD / 100.0):
        alert_payload = ALERT_TYPE_PRICE_SPIKE
    elif new_price < old_price and (
        notify_any or ((old_price - new_price) / old_price * 100) > PRICE_DROP_THRESHOLD
    ):
        alert_payload = ALERT_TYPE_PRICE_DROP

    if alert_payload is None:
        return None

    savings = round(max(0.0, old_price - new_price), 2)
    savings_percent = round((savings / old_price) * 100, 1) if old_price > 0 else 0.0
    trend = _trend_analysis(medicine)
    best_time = trend["best_time_to_buy"]

    if alert_payload == ALERT_TYPE_TARGET_REACHED:
        message = f"Target price reached! Buy now to save ₹{savings} ({savings_percent}%)."
    elif alert_payload == ALERT_TYPE_PRICE_SPIKE:
        message = f"Price spiked by more than {PRICE_SPIKE_THRESHOLD}% — wait for a better price."
    else:
        message = f"Price dropped! Buy now to save ₹{savings} ({savings_percent}%)."

    _insert_alert_log(user_id, medicine, alert_payload, old_price, new_price)
    _update_price_alert_current_price(user_id, medicine, new_price)

    return {
        "user_id": user_id,
        "medicine": medicine,
        "alert_type": alert_payload,
        "old_price": old_price,
        "new_price": new_price,
        "savings": savings,
        "savings_percent": savings_percent,
        "message": message,
        "best_time_to_buy": best_time,
        "triggered_at": _now_iso(),
    }


def run_price_alert_check(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    source = payload.get("source") if payload else SOURCE_SCRAPE
    with _get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT user_id, medicine, target_price, current_price, notify_on_any_drop FROM price_alerts WHERE is_active = 1"
        )
        alerts = [dict(row) for row in cur.fetchall()]

    if not alerts:
        return {"status": "no_active_alerts", "checked_at": _now_iso(), "triggered": []}

    triggered: List[Dict[str, Any]] = []
    for alert in alerts:
        medicine = alert["medicine"]
        latest_price = _get_latest_db_price(medicine)
        if latest_price is None:
            continue
        _insert_price_history(medicine, latest_price, source)
        triggered_alert = _process_price_alert(alert, latest_price)
        if triggered_alert:
            triggered.append(triggered_alert)

    return {
        "status": "checked",
        "checked_at": _now_iso(),
        "triggered": triggered,
        "count_checked": len(alerts),
    }


def start_price_alert_worker() -> None:
    def run_loop() -> None:
        while True:
            time.sleep(CHECK_INTERVAL_SECONDS)
            _simulate_price_updates()
            run_price_alert_check({"source": SOURCE_SIMULATED})

    thread = threading.Thread(target=run_loop, daemon=True, name="PriceAlertWorker")
    thread.start()
