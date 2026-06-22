import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# google-generativeai is an optional dependency (may not be installed in all environments)
try:
    import google.generativeai as genai  # type: ignore
except Exception:  # pragma: no cover
    genai = None

# Optional deps (only imported when endpoints are used)
try:
    import easyocr  # type: ignore
except Exception:  # pragma: no cover
    easyocr = None

try:
    from pdf2image import convert_from_path  # type: ignore
except Exception:  # pragma: no cover
    convert_from_path = None

try:
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
    Image = None

import sqlite3

BASE_DIR = Path(__file__).resolve().parent
TMP_UPLOAD_DIR = Path(os.environ.get("MEDIWISE_TMP_UPLOAD_DIR", "/tmp/mediwise_uploads/"))
TMP_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".pdf"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024

# Hardcode as requested
FREQUENCY_MAP: Dict[str, Dict[str, Any]] = {
    "1-0-1": {"human": "Morning and Night", "per_day": 2},
    "1-1-1": {"human": "Morning, Afternoon and Night", "per_day": 3},
    "0-0-1": {"human": "Night only", "per_day": 1},
    "1-0-0": {"human": "Morning only", "per_day": 1},
    "0-1-0": {"human": "Afternoon only", "per_day": 1},
    "1-1-0": {"human": "Morning and Afternoon", "per_day": 2},
    "SOS": {"human": "Only when needed", "per_day": 0},
    "BD": {"human": "Twice daily", "per_day": 2},
    "TDS": {"human": "Three times daily", "per_day": 3},
    "QID": {"human": "Four times daily", "per_day": 4},
    "OD": {"human": "Once daily", "per_day": 1},
    "HS": {"human": "At bedtime", "per_day": 1},
    "AC": {"human": "Before meals", "per_day": 3},
    "PC": {"human": "After meals", "per_day": 3},
}


def _get_db_path() -> str:
    # Keep consistent with db_setup.get_db_path() but avoid circular import
    return str(BASE_DIR / "medicines.db")


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(_get_db_path())


def _sanitize_filename(filename: str) -> str:
    filename = filename.replace("..", "")
    filename = re.sub(r"[^a-zA-Z0-9._-]+", "_", filename)
    return filename[:120] if len(filename) > 120 else filename


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _generate_prescription_id(user_id: Optional[str]) -> str:
    # Required shape: RX_YYYYMMDD_user123
    user_part = user_id if user_id else "anonymous"
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    safe_user = re.sub(r"[^a-zA-Z0-9_-]+", "_", user_part)[:40]
    return f"RX_{today}_{safe_user}"


def _validate_upload(filename: str, content_bytes: int) -> Tuple[bool, Optional[str]]:
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, "Only JPG, PNG, PDF supported"
    if content_bytes > MAX_FILE_SIZE_BYTES:
        return False, "Max file size is 10MB"
    return True, None


def _strip_code_fences(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```json\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^```\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```$", "", s)
    s = s.strip()
    return s


def _parse_gemini_json(text: str) -> Dict[str, Any]:
    cleaned = _strip_code_fences(text)
    return json.loads(cleaned)


def _frequency_decode(frequency: Optional[str]) -> Tuple[Optional[str], Optional[int]]:
    if not frequency:
        return None, None
    key = frequency.strip().upper()
    if key in FREQUENCY_MAP:
        return FREQUENCY_MAP[key]["human"], FREQUENCY_MAP[key]["per_day"]
    return None, None


def _parse_duration_days(duration_text: Optional[Union[str, int]]) -> Optional[int]:
    if duration_text is None:
        return None
    if isinstance(duration_text, int):
        return duration_text

    s = str(duration_text).strip().lower()
    if not s:
        return None

    # examples: "5 days", "1 week", "10 day", "2 weeks"
    m = re.search(r"(\d+)\s*(day|days|week|weeks|month|months)\b", s)
    if not m:
        return None

    n = int(m.group(1))
    unit = m.group(2)
    if unit.startswith("day"):
        return n
    if unit.startswith("week"):
        return n * 7
    if unit.startswith("month"):
        return n * 30
    return None


def _parse_dosage(dosage: Optional[str]) -> Optional[str]:
    if not dosage:
        return None
    # Keep a simple validation and normalization
    s = dosage.strip()
    # accept: "625mg", "650 mg", "5 mg/ml", "0.5mg", etc.
    if re.search(r"\b\d+(\.\d+)?\s*(mg|mcg|ml)\b", s, flags=re.IGNORECASE):
        return s.replace("  ", " ")
    return None


def _load_brand_to_generic() -> Dict[str, str]:
    path = BASE_DIR / "brand_to_generic.json"
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _map_brand_to_generic(brand_or_name: str, default_generic: Optional[str] = None) -> Optional[str]:
    if not brand_or_name:
        return default_generic
    brand_map = _load_brand_to_generic()
    key = brand_or_name.strip().lower()
    if key in brand_map:
        return brand_map[key]
    return default_generic


def _gemini_parse(
    raw_text: str,
    image_path: Optional[str],
    model_name: str = "gemini-1.5-flash",
) -> Dict[str, Any]:
    if genai is None:
        return {
            "medicines": [],
            "doctor_info": {"name": "", "clinic": "", "date": ""},
            "warnings": ["google-generativeai not installed; returning empty medicines."],
        }

    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        # No key: best-effort fallback using only OCR text.
        return {
            "medicines": [],
            "doctor_info": {"name": "", "clinic": "", "date": ""},
            "warnings": ["GEMINI_API_KEY not set; returning empty medicines."],
        }

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel(model_name)

    # Image part
    parts: List[Any] = [{"text": f"OCR text (may have errors): {raw_text}"}]

    if image_path:
        with open(image_path, "rb") as f:
            img_bytes = f.read()
        # For the SDK, easiest is to pass bytes with mime type
        ext = Path(image_path).suffix.lower()
        mime = "application/pdf" if ext == ".pdf" else "image/jpeg"
        if ext in [".png"]:
            mime = "image/png"
        parts.insert(0, {"inline_data": {"mime_type": mime, "data": img_bytes}})

    prompt = (
        "You are a medical prescription parser for Indian prescriptions.\n"
        "I will give you an OCR-extracted text from a prescription image\n"
        "and the original image. Your job is to extract all medicines\n"
        "accurately.\n\n"
        f"OCR text (may have errors): {raw_text}\n\n"
        "Instructions:\n"
        "1. Use the image to correct any OCR errors in medicine names\n"
        "2. Extract every medicine mentioned\n"
        "3. For frequency codes use Indian conventions:\n"
        "  1-0-1 = Morning + Night\n"
        "  1-1-1 = Morning + Afternoon + Night\n"
        "  0-0-1 = Night only\n"
        "  1-0-0 = Morning only\n"
        "  SOS   = As needed / when required\n"
        "  BD    = Twice daily\n"
        "  TDS   = Three times daily\n"
        "  OD    = Once daily\n"
        "4. Calculate total_tablets = frequency_per_day × duration_days\n"
        "5. Map brand names to generic names using Indian medicine knowledge\n"
        "6. Also extract doctor name, clinic name, and date if visible\n\n"
        "Return ONLY this JSON, no other text:\n"
        "{\n"
        "  'medicines': [\n"
        "    {\n"
        "      'medicine': '',\n"
        "      'brand_name': '',\n"
        "      'generic_name': '',\n"
        "      'dosage': '',\n"
        "      'frequency': '',\n"
        "      'frequency_human': '',\n"
        "      'duration': '',\n"
        "      'total_tablets': null,\n"
        "      'instructions': '',\n"
        "      'confidence': 'HIGH|MEDIUM|LOW'\n"
        "    }\n"
        "  ],\n"
        "  'doctor_info': {\n"
        "    'name': '',\n"
        "    'clinic': '',\n"
        "    'date': ''\n"
        "  },\n"
        "  'warnings': []\n"
        "}"
    )

    parts.append({"text": prompt})

    resp = model.generate_content(parts)
    text = getattr(resp, "text", None) or str(resp)

    # Retry parse once if fences are present
    try:
        return _parse_gemini_json(text)
    except Exception:
        cleaned = _strip_code_fences(text)
        return json.loads(cleaned)


def _run_easyocr(image_path: str) -> str:
    if easyocr is None:
        raise RuntimeError("easyocr is not installed")
    reader = easyocr.Reader(["en"], gpu=False)
    result = reader.readtext(image_path, detail=0)
    return "\n".join(result) if result else ""


def _convert_pdf_first_page_to_image(pdf_path: str) -> str:
    if convert_from_path is None or Image is None:
        raise RuntimeError("pdf2image/pillow not installed")
    pages = convert_from_path(pdf_path, first_page=1, last_page=1)
    if not pages:
        raise RuntimeError("PDF conversion produced no pages")
    img = pages[0]
    out_path = str(Path(TMP_UPLOAD_DIR) / f"{Path(pdf_path).stem}_page1.png")
    img.save(out_path, format="PNG")
    return out_path


def _compute_total_tablets(med: Dict[str, Any]) -> None:
    frequency_code = (med.get("frequency") or "").strip().upper()
    _, per_day = _frequency_decode(frequency_code)
    duration = med.get("duration")
    duration_days = _parse_duration_days(duration)
    if per_day is None:
        return
    if duration_days is None:
        return
    if per_day == 0:
        med["total_tablets"] = None
        return
    med["total_tablets"] = per_day * duration_days


def _post_process_gemini_output(raw: Dict[str, Any], raw_text: str, warnings: List[str]) -> Dict[str, Any]:
    medicines_out: List[Dict[str, Any]] = []
    meds_in = raw.get("medicines") or []
    doctor_info = raw.get("doctor_info") or {}
    out_warnings = list(raw.get("warnings") or [])
    for w in warnings:
        if w not in out_warnings:
            out_warnings.append(w)

    # Validate medicines via basic rules requested (and brand→generic mapping)
    for med in meds_in:
        if not isinstance(med, dict):
            continue
        med_name = med.get("medicine") or med.get("brand_name") or ""
        brand_name = med.get("brand_name") or med_name
        dosage = _parse_dosage(med.get("dosage"))
        frequency_code = (med.get("frequency") or "").strip().upper()
        freq_human, _ = _frequency_decode(frequency_code)

        duration_str = med.get("duration") or None
        if duration_str is not None and str(duration_str).strip() == "":
            duration_str = None

        confidence = med.get("confidence") or "LOW"
        confidence = confidence if confidence in {"HIGH", "MEDIUM", "LOW"} else "LOW"

        generic = med.get("generic_name") or None
        mapped = _map_brand_to_generic(brand_name, default_generic=generic)
        if mapped:
            generic = mapped

        out_med: Dict[str, Any] = {
            "medicine": med.get("medicine") or med_name or None,
            "brand_name": brand_name or None,
            "generic_name": generic,
            "dosage": dosage,
            "frequency": med.get("frequency") or frequency_code or None,
            "frequency_human": med.get("frequency_human") or freq_human,
            "duration": duration_str,
            "total_tablets": med.get("total_tablets"),
            "instructions": med.get("instructions") or None,
            "confidence": confidence,
        }

        # If duration missing, warn and keep confidence MEDIUM/LOW
        if out_med.get("duration") is None and out_med.get("frequency") and out_med.get("frequency") != "SOS":
            # per spec warn duration not mentioned
            if "Duration not mentioned" not in " ".join(out_warnings):
                out_warnings.append(f"Duration not mentioned for {out_med.get('brand_name')} — assuming per frequency as written")

        # Calculate total_tablets if missing
        if out_med.get("total_tablets") is None:
            _compute_total_tablets(out_med)

        medicines_out.append(out_med)

    # Overall warning if empty
    if not medicines_out:
        out_warnings.append("No medicines detected. Please upload a clearer image")

    return {
        "medicines": medicines_out,
        "doctor_info": {
            "name": doctor_info.get("name") or "",
            "clinic": doctor_info.get("clinic") or "",
            "date": doctor_info.get("date") or "",
        },
        "warnings": out_warnings,
    }


def _get_db_prescription_history(user_id: str) -> List[Dict[str, Any]]:
    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT prescription_id, raw_text, medicines_json, confidence, extracted_at
            FROM prescriptions
            WHERE user_id = ?
            ORDER BY extracted_at DESC
            """,
            (user_id,),
        )
        rows = cur.fetchall()

    out: List[Dict[str, Any]] = []
    for prescription_id, raw_text, medicines_json, confidence, extracted_at in rows:
        meds = []
        try:
            parsed = json.loads(medicines_json) if medicines_json else {}
            meds = parsed.get("medicines") or parsed.get("medicines_json") or parsed.get("medicines_list") or []
        except Exception:
            meds = []
        out.append(
            {
                "prescription_id": prescription_id,
                "raw_text": raw_text,
                "medicines": meds,
                "confidence": confidence,
                "extracted_at": extracted_at,
            }
        )
    return out


def _save_prescription(
    prescription_id: str,
    user_id: Optional[str],
    raw_text: str,
    full_output: Dict[str, Any],
    image_path: Optional[str],
    confidence: str,
) -> None:
    medicines_json_str = json.dumps(full_output, ensure_ascii=False)

    with _conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO prescriptions
              (prescription_id, user_id, raw_text, medicines_json, image_path, confidence, extracted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                prescription_id,
                user_id,
                raw_text,
                medicines_json_str,
                image_path,
                confidence,
                _now_iso(),
            ),
        )
        conn.commit()


def _overall_confidence(meds: List[Dict[str, Any]]) -> str:
    if not meds:
        return "LOW"
    if any(m.get("confidence") == "LOW" for m in meds):
        return "LOW"
    if any(m.get("confidence") == "MEDIUM" for m in meds):
        return "MEDIUM"
    return "HIGH"


async def ocr_from_upload(file_upload: Any, user_id: Optional[str]) -> Dict[str, Any]:
    filename = getattr(file_upload, "filename", "upload")
    content = await file_upload.read()
    content_size = len(content)

    ok, err = _validate_upload(filename, content_size)
    if not ok:
        raise ValueError(err)

    ext = Path(filename).suffix.lower()
    saved_path = TMP_UPLOAD_DIR / _sanitize_filename(filename)
    with open(saved_path, "wb") as f:
        f.write(content)

    # Convert PDF to image
    image_for_ocr_path: Optional[str] = None
    converted_temp_path: Optional[str] = None
    if ext == ".pdf":
        try:
            converted_temp_path = _convert_pdf_first_page_to_image(str(saved_path))
            image_for_ocr_path = converted_temp_path
        except Exception:
            # Spec: 400 Could not process PDF...
            raise ValueError("Could not process PDF. Try uploading as JPG instead")
    else:
        image_for_ocr_path = str(saved_path)

    raw_text = ""
    warnings: List[str] = []
    # OCR step
    try:
        raw_text = _run_easyocr(image_for_ocr_path) if image_for_ocr_path else ""
        if not raw_text.strip():
            warnings.append("EasyOCR produced no text")
    except Exception:
        warnings.append("EasyOCR failed — skipping OCR text; sending image only")
        raw_text = ""

    # Gemini step (Vision cleanup)
    try:
        gemini_raw = _gemini_parse(raw_text=raw_text, image_path=image_for_ocr_path)
        structured = _post_process_gemini_output(gemini_raw, raw_text=raw_text, warnings=warnings)
    except Exception:
        # If Gemini fails, still return empty medicines and warnings
        structured = {
            "medicines": [],
            "doctor_info": {"name": "", "clinic": "", "date": ""},
            "warnings": warnings + ["Gemini Vision failed; returning empty medicines."],
        }

    # Confidence
    meds = structured.get("medicines") or []
    confidence = _overall_confidence(meds)

    prescription_id = _generate_prescription_id(user_id)
    extracted_at = _now_iso()

    # Assemble full output JSON (spec-required shape)
    # Ensure raw_text uses newline-separated OCR output
    full_output: Dict[str, Any] = {
        "prescription_id": prescription_id,
        "extracted_at": extracted_at,
        "confidence": confidence,
        "raw_text": raw_text.strip(),
        **structured,
    }

    # Post-processing validation: ensure warnings about duration not mentioned etc already in structured
    # Save to DB
    try:
        _save_prescription(
            prescription_id=prescription_id,
            user_id=user_id,
            raw_text=raw_text.strip(),
            full_output=full_output,
            image_path=str(saved_path),
            confidence=confidence,
        )
    except Exception:
        # DB save failure should not break OCR response
        warnings.append("Failed to persist prescription to DB")

    return full_output


async def ocr_from_raw_text(raw_text: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Testing endpoint: no image required.
    """
    raw_text = raw_text or ""
    warnings: List[str] = []
    structured: Dict[str, Any]

    # Gemini cleanup/structuring (image omitted)
    try:
        gemini_raw = _gemini_parse(raw_text=raw_text, image_path=None)
        structured = _post_process_gemini_output(gemini_raw, raw_text=raw_text, warnings=warnings)
    except Exception:
        structured = {
            "medicines": [],
            "doctor_info": {"name": "", "clinic": "", "date": ""},
            "warnings": warnings + ["Gemini Vision failed; returning empty medicines."],
        }

    meds = structured.get("medicines") or []
    confidence = _overall_confidence(meds)

    prescription_id = _generate_prescription_id(user_id)
    extracted_at = _now_iso()

    full_output: Dict[str, Any] = {
        "prescription_id": prescription_id,
        "extracted_at": extracted_at,
        "confidence": confidence,
        "raw_text": raw_text.strip(),
        **structured,
    }

    # Save to DB with image_path = None
    try:
        _save_prescription(
            prescription_id=prescription_id,
            user_id=user_id,
            raw_text=raw_text.strip(),
            full_output=full_output,
            image_path=None,
            confidence=confidence,
        )
    except Exception:
        # DB save failure should not break response
        full_output.setdefault("warnings", [])
        if isinstance(full_output["warnings"], list):
            full_output["warnings"].append("Failed to persist prescription to DB")

    return full_output


def get_ocr_history(user_id: str) -> List[Dict[str, Any]]:
    return _get_db_prescription_history(user_id)
