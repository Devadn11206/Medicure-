import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# google-generativeai is optional
try:
    import google.generativeai as genai  # type: ignore
except Exception:  # pragma: no cover
    genai = None

# Optional deps (used for PDF handling)
try:
    from pdf2image import convert_from_path  # type: ignore
except Exception:  # pragma: no cover
    convert_from_path = None

try:
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
    Image = None

from .db_setup import get_db_path, init_db

DB_PATH = get_db_path()

TMP_DIR = Path(os.environ.get("MEDIWISE_TMP_BILLS_DIR", "/tmp/mediwise_audits/"))
TMP_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _strip_code_fences(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^```json\s*", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"^```\s*", "", s, flags=re.IGNORECASE).strip()
    s = re.sub(r"\s*```$", "", s).strip()
    return s


def _parse_gemini_json(text: str) -> Dict[str, Any]:
    return json.loads(_strip_code_fences(text))


def _gemini_enabled() -> bool:
    return genai is not None and bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))


def _gemini_model(model_name: str = "gemini-1.5-flash"):
    if genai is None:
        raise RuntimeError("google-generativeai not installed")
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")
    genai.configure(api_key=api_key)
    return genai.GenerativeModel(model_name)


def _validate_upload(filename: str, content_bytes: int) -> Optional[str]:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return "Only JPG, PNG, PDF supported"
    if content_bytes > MAX_FILE_SIZE_BYTES:
        return "Max file size is 10MB"
    return None


def _convert_pdf_first_page(pdf_path: str) -> str:
    if convert_from_path is None or Image is None:
        raise RuntimeError("pdf2image/pillow not installed")
    pages = convert_from_path(pdf_path, first_page=1, last_page=1)
    if not pages:
        raise RuntimeError("PDF conversion produced no pages")
    img = pages[0]
    out_path = TMP_DIR / f"{Path(pdf_path).stem}_page1.png"
    img.save(str(out_path), format="PNG")
    return str(out_path)


def _audit_id_for(user_id: Optional[str], audit_dt_iso: str) -> str:
    safe_user = (user_id or "anonymous").strip().replace(" ", "_")[:40]
    audit_date = audit_dt_iso.split("T")[0]  # YYYY-MM-DD
    return f"AUDIT_{audit_date.replace('-', '')}_{safe_user}"


def _normalize_name(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def _is_vague_charge(name: str) -> bool:
    n = (name or "").lower()
    vague_keywords = [
        "miscellaneous",
        "sundry",
        "other charges",
        "service charge",
        "nursing charges",
        "ward charges",
        "consumables",
        "misc charges",
        "misc",
    ]
    return any(k in n for k in vague_keywords)


def _load_medicine_price_from_db(medicine_name: str) -> Optional[float]:
    """
    Defensive: only compares if a medicines table with price_inr exists.
    Current repo might not have it, so return None safely.
    """
    if not os.path.exists(DB_PATH):
        return None
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='medicines'")
        if not cur.fetchone():
            return None
        cur.execute(
            "SELECT price_inr FROM medicines WHERE lower(name)=lower(?) LIMIT 1",
            (medicine_name,),
        )
        row = cur.fetchone()
        if row and row[0] is not None:
            return float(row[0])
        return None
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _gemini_extract_bill(image_path: str, model_name: str = "gemini-1.5-flash") -> Dict[str, Any]:
    if not _gemini_enabled():
        return {
            "hospital_name": None,
            "bill_date": None,
            "patient_name": None,
            "total_amount": None,
            "items": [],
        }

    model = _gemini_model(model_name)

    extraction_prompt = (
        "You are a medical billing expert analyzing an Indian hospital bill.\n"
        "Extract every single line item from this bill image.\n\n"
        "Return ONLY this JSON, no other text:\n"
        "{\n"
        "  'hospital_name': '',\n"
        "  'bill_date': '',\n"
        "  'patient_name': '',\n"
        "  'total_amount': <number>,\n"
        "  'items': [\n"
        "    {\n"
        "      'name': '<exact item name as written on bill>',\n"
        "      'quantity': <number or 1 if not specified>,\n"
        "      'unit_price': <price per unit>,\n"
        "      'total_price': <total for this line>,\n"
        "      'date': '<date if mentioned, else null>',\n"
        "      'category': 'room|surgery|medicine|diagnostics|nursing|consultation|consumables|other'\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "If any field is missing set to null.\n"
        "For medicines include the strength if visible (e.g. Paracetamol 500mg).\n"
        "Do not skip any line item even if amount is small."
    )

    parts: List[Any] = []
    ext = Path(image_path).suffix.lower()
    mime = "application/pdf" if ext == ".pdf" else "image/jpeg"
    if ext == ".png":
        mime = "image/png"

    with open(image_path, "rb") as f:
        img_bytes = f.read()

    parts.append({"inline_data": {"mime_type": mime, "data": img_bytes}})
    parts.append({"text": extraction_prompt})

    resp = model.generate_content(parts)
    text = getattr(resp, "text", None) or str(resp)
    return _parse_gemini_json(text)


def _gemini_unnecessary_tests(
    bill_type: str,
    items_json: List[Dict[str, Any]],
    model_name: str = "gemini-1.5-flash",
) -> Dict[str, Any]:
    if not _gemini_enabled():
        return {"unnecessary": []}
    model = _gemini_model(model_name)

    review_prompt = (
        "You are a senior Indian doctor reviewing a hospital bill for a\n"
        f"{bill_type} patient. These are the billed procedures and tests:\n"
        f"{json.dumps(items_json, ensure_ascii=False)}\n\n"
        "Identify any tests or procedures that are clinically questionable\n"
        "— meaning they may not be necessary for a routine case of this type.\n\n"
        "Consider Indian healthcare context and common over-testing patterns.\n"
        "Only flag items where over-testing is genuinely possible.\n"
        "Do not flag clearly necessary items.\n\n"
        "Return ONLY this JSON:\n"
        "{\n"
        "  'unnecessary': [\n"
        "    {\n"
        "      'name': '<exact item name>',\n"
        "      'reason': '<one sentence why it may be unnecessary>',\n"
        "      'severity': 'HIGH|MODERATE|LOW'\n"
        "    }\n"
        "  ]\n"
        "}"
    )

    resp = model.generate_content(review_prompt)
    text = getattr(resp, "text", None) or str(resp)
    try:
        return _parse_gemini_json(text)
    except Exception:
        return {"unnecessary": []}


def _gemini_dispute_letter(
    flags_summary: str,
    amount: float,
    model_name: str = "gemini-1.5-flash",
) -> Optional[str]:
    if not _gemini_enabled():
        return None
    model = _gemini_model(model_name)

    prompt = (
        "Write a formal dispute letter from a patient to a hospital\n"
        "billing department in India. The patient wants to dispute\n"
        f"these specific charges: {flags_summary}\n"
        f"Total disputed amount: ₹{amount}\n"
        "Keep it professional, firm, and under 200 words.\n"
        "Reference Consumer Protection Act 1986 where applicable.\n"
        "Return plain text only."
    )

    resp = model.generate_content(prompt)
    text = getattr(resp, "text", None) or str(resp)
    return str(text).strip() if text else None


def _detect_duplicates(extracted_items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], float, int]:
    # Group by normalized name + date (date may be null)
    groups: Dict[Tuple[str, Optional[str]], List[Dict[str, Any]]] = {}
    for it in extracted_items:
        key = (_normalize_name(it.get("name") or ""), it.get("date"))
        groups.setdefault(key, []).append(it)

    flags: List[Dict[str, Any]] = []
    flag_id = 1
    potential_overcharge = 0.0

    for (norm_name, date_key), g in groups.items():
        if len(g) <= 1:
            continue
        # use unit_price from first if possible
        unit_price = float(g[0].get("unit_price") or 0.0)
        occurrences = len(g)
        over = max(0.0, (occurrences - 1) * unit_price)
        item_name = g[0].get("name") or norm_name or "Unknown"

        billed_amount = sum(float(x.get("total_price") or 0.0) for x in g)

        flags.append(
            {
                "flag_id": flag_id,
                "type": "DUPLICATE",
                "severity": "HIGH",
                "item": item_name,
                "billed_amount": billed_amount,
                "fair_amount": unit_price,
                "overcharge": over,
                "details": f"{item_name} appears multiple times for the same date. Only one should be charged.",
                "how_to_dispute": "Show both line items to billing department and request removal of duplicate charge.",
                "evidence": f"{date_key}" if date_key else "Multiple occurrences in bill",
            }
        )
        flag_id += 1
        potential_overcharge += over

    return flags, potential_overcharge, flag_id


def _detect_overpriced(extracted_items: List[Dict[str, Any]], starting_flag_id: int) -> Tuple[List[Dict[str, Any]], float, int]:
    flags: List[Dict[str, Any]] = []
    confirmed_overcharge = 0.0
    flag_id = starting_flag_id

    for it in extracted_items:
        category = it.get("category") or ""
        name = it.get("name") or ""
        qty = it.get("quantity") or 1
        unit_price = float(it.get("unit_price") or 0.0)
        total_price = float(it.get("total_price") or 0.0)

        # Medicines
        if category == "medicine":
            mrp = _load_medicine_price_from_db(name)
            if mrp is None:
                continue
            if unit_price > 3.0 * float(mrp):
                over = (unit_price - float(mrp)) * float(qty or 1)
                flags.append(
                    {
                        "flag_id": flag_id,
                        "type": "OVERPRICED",
                        "severity": "HIGH",
                        "item": name,
                        "billed_amount": total_price,
                        "fair_amount": float(mrp) * float(qty or 1),
                        "overcharge": over,
                        "details": f"Hospital billed {unit_price:.2f} per unit vs market MRP {mrp:.2f}.",
                        "how_to_dispute": "Request MRP-based billing. Ask billing department for MRP proof for the billed units.",
                        "evidence": f"Market/DB price: ₹{mrp:.2f}",
                    }
                )
                flag_id += 1
                confirmed_overcharge += float(over)

        # Consumables heuristic
        if category == "consumables":
            if unit_price > 200:
                flags.append(
                    {
                        "flag_id": flag_id,
                        "type": "OVERPRICED",
                        "severity": "MODERATE",
                        "item": name,
                        "billed_amount": total_price,
                        "fair_amount": None,
                        "overcharge": None,
                        "details": f"Consumable '{name}' appears potentially overpriced based on per-unit billing patterns.",
                        "how_to_dispute": "Request itemized MRP/invoice for the consumable from billing department.",
                        "evidence": f"Per-unit billed: ₹{unit_price:.2f}",
                    }
                )
                flag_id += 1

    return flags, confirmed_overcharge, flag_id


def _detect_unnecessary_via_gemini(
    bill_type: str,
    extracted_items: List[Dict[str, Any]],
    starting_flag_id: int,
) -> Tuple[List[Dict[str, Any]], int]:
    """
    UNNECESSARY flags are intentionally best-effort.
    If Gemini fails, we return empty list (per spec).
    """
    flags: List[Dict[str, Any]] = []
    flag_id = starting_flag_id

    try:
        review = _gemini_unnecessary_tests(bill_type=bill_type, items_json=extracted_items)
        for u in (review.get("unnecessary") or []):
            flags.append(
                {
                    "flag_id": flag_id,
                    "type": "UNNECESSARY",
                    "severity": u.get("severity") or "LOW",
                    "item": u.get("name") or "Unknown",
                    "billed_amount": None,
                    "fair_amount": None,
                    "overcharge": 0,
                    "details": u.get("reason") or "Clinically questionable test/procedure",
                    "how_to_dispute": "Ask your doctor for written justification explaining why the test/procedure was necessary.",
                    "evidence": "AI clinical review",
                }
            )
            flag_id += 1
    except Exception:
        # Spec: if Gemini fails on clinical review → skip UNNECESSARY detection
        return [], starting_flag_id

    return flags, flag_id


def _compute_dispute_confidence(
    flags: List[Dict[str, Any]],
    potential_overcharge: float,
    confirmed_overcharge: float,
) -> str:
    dup_count = sum(1 for f in flags if f.get("type") == "DUPLICATE")
    unclear_count = sum(1 for f in flags if f.get("type") == "UNCLEAR")
    over_count = sum(1 for f in flags if f.get("type") == "OVERPRICED")
    unnecessary_count = sum(1 for f in flags if f.get("type") == "UNNECESSARY")

    # Priority: duplicates/large money -> higher confidence
    if confirmed_overcharge > 1000 or (dup_count >= 2):
        return "HIGH"
    if potential_overcharge >= 200 or (dup_count >= 1 and (unclear_count >= 1 or unnecessary_count >= 1)):
        return "MEDIUM"
    if over_count > 0 or unclear_count > 0:
        return "LOW"
    return "LOW"


def _final_recommendation(dispute_confidence: str, confirmed_overcharge: float) -> str:
    if dispute_confidence in {"HIGH", "MEDIUM"} and confirmed_overcharge > 0:
        return "DISPUTE"
    if dispute_confidence in {"HIGH", "MEDIUM"}:
        return "DISPUTE"
    return "ACCEPTABLE"


def _ensure_schema() -> None:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_reports (
                audit_id TEXT PRIMARY KEY,
                user_id TEXT,
                bill_type TEXT,
                original_file_name TEXT,
                total_billed REAL,
                potential_overcharge REAL,
                confirmed_overcharge REAL,
                dispute_confidence TEXT,
                recommendation TEXT,
                flags_json TEXT,
                items_json TEXT,
                created_at TIMESTAMP
            );
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_dispute_letters (
                audit_id TEXT PRIMARY KEY,
                user_id TEXT,
                letter_text TEXT,
                created_at TIMESTAMP
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _audit_save_to_db(
    audit_id: str,
    user_id: Optional[str],
    bill_type: str,
    original_file_name: str,
    total_billed: Optional[float],
    potential_overcharge: float,
    confirmed_overcharge: float,
    dispute_confidence: str,
    recommendation: str,
    flags: List[Dict[str, Any]],
    extracted_items: List[Dict[str, Any]],
) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO audit_reports (
                audit_id, user_id, bill_type, original_file_name,
                total_billed, potential_overcharge, confirmed_overcharge,
                dispute_confidence, recommendation,
                flags_json, items_json, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                audit_id,
                user_id,
                bill_type,
                original_file_name,
                total_billed,
                potential_overcharge,
                confirmed_overcharge,
                dispute_confidence,
                recommendation,
                json.dumps(flags, ensure_ascii=False),
                json.dumps(extracted_items, ensure_ascii=False),
                _now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _audit_save_letter_to_db(
    audit_id: str,
    user_id: Optional[str],
    letter_text: str,
) -> None:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO audit_dispute_letters (
                audit_id, user_id, letter_text, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (audit_id, user_id, letter_text, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def _detect_duplicates_and_overcharge(extracted_items: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], float, float]:
    dup_flags, potential_overcharge, next_id = _detect_duplicates(extracted_items)
    over_flags, confirmed_overcharge, _ = _detect_overpriced(extracted_items, starting_flag_id=next_id)
    return dup_flags + over_flags, potential_overcharge, confirmed_overcharge


def _detect_unclear_flags(extracted_items: List[Dict[str, Any]], starting_flag_id: int) -> Tuple[List[Dict[str, Any]], int]:
    flags, next_id = _detect_unclear(extracted_items, starting_flag_id)
    return flags, next_id


def _run_audit_logic(
    bill_type: str,
    original_file_name: str,
    user_id: Optional[str],
    extracted: Dict[str, Any],
    include_letter: bool,
) -> Dict[str, Any]:
    _ensure_schema()

    extracted_items = extracted.get("items") or []
    total_amount = extracted.get("total_amount")
    try:
        total_amount = float(total_amount) if total_amount is not None else None
    except Exception:
        total_amount = None

    audit_dt_iso = _now_iso()
    audit_id = _audit_id_for(user_id, audit_dt_iso)

    # Step order: duplicates+overpriced -> unclear -> unnecessary (gemini optional) -> confidence+recommendation
    base_flags, potential_overcharge, confirmed_overcharge = _detect_duplicates_and_overcharge(extracted_items)
    next_flag_id = max((f.get("flag_id") for f in base_flags), default=0) + 1

    unclear_flags, next_flag_id = _detect_unclear_flags(extracted_items, starting_flag_id=next_flag_id)
    base_flags.extend(unclear_flags)

    # Optional Gemini call for unnecessary tests (best-effort)
    unnecessary_flags, next_flag_id = _detect_unnecessary_via_gemini(
        bill_type=bill_type,
        extracted_items=extracted_items,
        starting_flag_id=next_flag_id,
    )
    base_flags.extend(unnecessary_flags)

    dispute_confidence = _compute_dispute_confidence(
        flags=base_flags,
        potential_overcharge=potential_overcharge,
        confirmed_overcharge=confirmed_overcharge,
    )
    recommendation = _final_recommendation(dispute_confidence, confirmed_overcharge)

    # Save report
    _audit_save_to_db(
        audit_id=audit_id,
        user_id=user_id,
        bill_type=bill_type,
        original_file_name=original_file_name,
        total_billed=total_amount,
        potential_overcharge=potential_overcharge,
        confirmed_overcharge=confirmed_overcharge,
        dispute_confidence=dispute_confidence,
        recommendation=recommendation,
        flags=base_flags,
        extracted_items=extracted_items,
    )

    # Optional dispute letter via Gemini (best-effort; should not break endpoint)
    letter_text: Optional[str] = None
    if include_letter:
        try:
            flags_summary = ", ".join([f"{f.get('type')}({f.get('item')})" for f in base_flags[:20]])
            letter_text = _gemini_dispute_letter(
                flags_summary=flags_summary,
                amount=confirmed_overcharge or 0.0,
            )
            if letter_text:
                _audit_save_letter_to_db(audit_id=audit_id, user_id=user_id, letter_text=letter_text)
        except Exception:
            letter_text = None

    return {
        "audit_id": audit_id,
        "user_id": user_id,
        "bill_type": bill_type,
        "summary": {
            "total_billed": total_amount,
            "potential_overcharge": potential_overcharge,
            "confirmed_overcharge": confirmed_overcharge,
            "dispute_confidence": dispute_confidence,
            "recommendation": recommendation,
            "total_flags": len(base_flags),
        },
        "flags": base_flags,
        "items": extracted_items,
        "dispute_letter_generated": bool(letter_text),
    }


def _detect_unclear(extracted_items: List[Dict[str, Any]], starting_flag_id: int) -> Tuple[List[Dict[str, Any]], int]:
    """
    Detect UNCLEAR/vague charges.
    Mark if amount > 500 and the item name looks like a vague bucket charge.
    """
    flags: List[Dict[str, Any]] = []
    flag_id = starting_flag_id

    for it in extracted_items:
        item_name = it.get("name") or ""
        category = it.get("category") or ""
        amount = float(it.get("total_price") or 0.0)

        if amount <= 500:
            continue
        if not _is_vague_charge(item_name, category):
            continue

        flags.append(
            {
                "flag_id": flag_id,
                "type": "UNCLEAR",
                "severity": "LOW",
                "item": item_name,
                "billed_amount": amount,
                "fair_amount": None,
                "overcharge": None,
                "details": f"'{item_name}' is billed as a vague charge without clear itemization.",
                "how_to_dispute": "Request complete itemized breakdown in writing. Vague charges can be disputed.",
                "evidence": "No supporting description or sub-items provided",
            }
        )
        flag_id += 1

    return flags, flag_id


def audit_from_bill_upload(
    file_bytes: bytes,
    filename: str,
    bill_type: str,
    user_id: Optional[str],
    include_dispute_letter: bool,
) -> Dict[str, Any]:
    """
    Main entry for /audit route.
    Handles upload validation + Gemini extraction + deterministic flags.
    """
    validation_error = _validate_upload(filename, len(file_bytes))
    if validation_error:
        raise ValueError(validation_error)

    ext = Path(filename).suffix.lower()
    saved_path = TMP_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{abs(hash(filename))}{ext}"
    with open(saved_path, "wb") as f:
        f.write(file_bytes)

    # If PDF -> first-page conversion for Gemini
    if ext == ".pdf":
        if convert_from_path is None:
            raise RuntimeError("pdf2image/pillow not installed")
        image_path_for_gemini = _convert_pdf_first_page(str(saved_path))
    else:
        image_path_for_gemini = str(saved_path)

    extracted: Dict[str, Any]
    if _gemini_enabled():
        extracted = _gemini_extract_bill(image_path_for_gemini)
    else:
        # Defensive fallback: still return empty items rather than erroring
        extracted = {
            "hospital_name": None,
            "bill_date": None,
            "patient_name": None,
            "total_amount": None,
            "items": [],
        }

    # If Gemini extraction fails (spec: 500 for extraction failure)
    # Here we treat “invalid json-like parse errors” as failure; _gemini_extract_bill
    # already returns defaults when Gemini isn't enabled.
    extracted_items = extracted.get("items") or []
    if not isinstance(extracted_items, list):
        raise RuntimeError("Gemini extraction failure (items not a list)")

    # Spec: if no items -> 200 with empty flags warning
    if len(extracted_items) == 0:
        audit_dt_iso = _now_iso()
        audit_id = _audit_id_for(user_id, audit_dt_iso)
        _ensure_schema()
        _audit_save_to_db(
            audit_id=audit_id,
            user_id=user_id,
            bill_type=bill_type,
            original_file_name=filename,
            total_billed=None,
            potential_overcharge=0.0,
            confirmed_overcharge=0.0,
            dispute_confidence="LOW",
            recommendation="ACCEPTABLE",
            flags=[],
            extracted_items=[],
        )
        return {
            "audit_id": audit_id,
            "user_id": user_id,
            "bill_type": bill_type,
            "summary": {
                "total_billed": None,
                "potential_overcharge": 0.0,
                "confirmed_overcharge": 0.0,
                "dispute_confidence": "LOW",
                "recommendation": "ACCEPTABLE",
                "total_flags": 0,
            },
            "flags": [],
            "items": [],
            "dispute_letter_generated": False,
            "warnings": ["No bill line items were detected from the uploaded document."],
        }

    report = _run_audit_logic(
        bill_type=bill_type,
        original_file_name=filename,
        user_id=user_id,
        extracted=extracted,
        include_letter=include_dispute_letter,
    )
    return report


def audit_from_manual_items(
    items: List[Dict[str, Any]],
    bill_type: str,
    user_id: Optional[str],
    include_dispute_letter: bool,
) -> Dict[str, Any]:
    extracted: Dict[str, Any] = {
        "hospital_name": None,
        "bill_date": None,
        "patient_name": None,
        "total_amount": None,
        "items": items or [],
    }
    if not isinstance(extracted["items"], list):
        raise ValueError("items must be a list")
    if len(extracted["items"]) == 0:
        audit_dt_iso = _now_iso()
        audit_id = _audit_id_for(user_id, audit_dt_iso)
        _ensure_schema()
        _audit_save_to_db(
            audit_id=audit_id,
            user_id=user_id,
            bill_type=bill_type,
            original_file_name="manual",
            total_billed=None,
            potential_overcharge=0.0,
            confirmed_overcharge=0.0,
            dispute_confidence="LOW",
            recommendation="ACCEPTABLE",
            flags=[],
            extracted_items=[],
        )
        return {
            "audit_id": audit_id,
            "user_id": user_id,
            "bill_type": bill_type,
            "summary": {
                "total_billed": None,
                "potential_overcharge": 0.0,
                "confirmed_overcharge": 0.0,
                "dispute_confidence": "LOW",
                "recommendation": "ACCEPTABLE",
                "total_flags": 0,
            },
            "flags": [],
            "items": [],
            "dispute_letter_generated": False,
            "warnings": ["No bill line items were provided in manual input."],
        }

    report = _run_audit_logic(
        bill_type=bill_type,
        original_file_name="manual",
        user_id=user_id,
        extracted=extracted,
        include_letter=include_dispute_letter,
    )
    return report


def get_audit_report(audit_id: str) -> Dict[str, Any]:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT audit_id, user_id, bill_type, original_file_name, total_billed, potential_overcharge, confirmed_overcharge, dispute_confidence, recommendation, flags_json, items_json, created_at FROM audit_reports WHERE audit_id=?", (audit_id,))
        row = cur.fetchone()
        if not row:
            raise KeyError("Audit not found")

        return {
            "audit_id": row[0],
            "user_id": row[1],
            "bill_type": row[2],
            "original_file_name": row[3],
            "summary": {
                "total_billed": row[4],
                "potential_overcharge": row[5],
                "confirmed_overcharge": row[6],
                "dispute_confidence": row[7],
                "recommendation": row[8],
                "total_flags": len(json.loads(row[9] or "[]")),
            },
            "flags": json.loads(row[9] or "[]"),
            "items": json.loads(row[10] or "[]"),
            "created_at": row[11],
        }
    finally:
        conn.close()


def get_dispute_letter(audit_id: str) -> str:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT letter_text FROM audit_dispute_letters WHERE audit_id=?", (audit_id,))
        row = cur.fetchone()
        if not row or not row[0]:
            raise KeyError("Dispute letter not found")
        return row[0]
    finally:
        conn.close()


def get_audit_history(user_id: str) -> List[Dict[str, Any]]:
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("SELECT audit_id, bill_type, original_file_name, total_billed, potential_overcharge, confirmed_overcharge, dispute_confidence, recommendation, created_at FROM audit_reports WHERE user_id=? ORDER BY created_at DESC", (user_id,))
        rows = cur.fetchall()
        result: List[Dict[str, Any]] = []
        for r in rows:
            result.append(
                {
                    "audit_id": r[0],
                    "bill_type": r[1],
                    "original_file_name": r[2],
                    "summary": {
                        "total_billed": r[3],
                        "potential_overcharge": r[4],
                        "confirmed_overcharge": r[5],
                        "dispute_confidence": r[6],
                        "recommendation": r[7],
                    },
                    "created_at": r[8],
                }
            )
        return result
    finally:
        conn.close()
