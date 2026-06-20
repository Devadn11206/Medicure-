import itertools
import re
import sqlite3
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Tuple, Any, Optional

import requests

from .db_setup import get_db_path, init_db


BRAND_TO_GENERIC = {
    # Paracetamol
    "crocin": "paracetamol",
    "dolo": "paracetamol",
    "calpol": "paracetamol",
    # Aspirin
    "ecospirin": "aspirin",
    "ecosprin": "aspirin",
    "disprin": "aspirin",
    # Metformin
    "glycomet": "metformin",
    "glucophage": "metformin",
    # Amoxicillin (brand combos vary; keep at minimum requested)
    "augmentin": "amoxicillin",
    "moxclav": "amoxicillin",
    # Prednisolone
    "omnacortil": "prednisolone",
    "wysolone": "prednisolone",
    # Ibuprofen + Paracetamol
    "combiflam": "ibuprofen + paracetamol",
    # Pantoprazole
    "pan": "pantoprazole",
    "pantop": "pantoprazole",
    # Omeprazole
    "omez": "omeprazole",
    # Azithromycin
    "azee": "azithromycin",
    "zithromax": "azithromycin",
    # Atorvastatin
    "lipitor": "atorvastatin",
}


# Safety-critical known interactions (local fallback)
# Keys are normalized lowercase drug names.
KNOWN_INTERACTIONS: Dict[Tuple[str, str], Dict[str, str]] = {
    ("warfarin", "aspirin"): {
        "risk_level": "HIGH",
        "description": "Increased bleeding risk. Combined use of warfarin and aspirin significantly raises hemorrhage risk.",
        "recommendation": "Consult doctor before taking together.",
    },
    ("warfarin", "ibuprofen"): {
        "risk_level": "HIGH",
        "description": "Increased bleeding risk with NSAIDs such as ibuprofen when combined with warfarin.",
        "recommendation": "Avoid combination unless doctor advises; monitor closely.",
    },
    ("ssri", "tramadol"): {
        "risk_level": "MODERATE",
        "description": "Potential increased risk of serotonin-related effects when SSRIs are combined with tramadol.",
        "recommendation": "Use only under medical supervision.",
    },
    ("methotrexate", "nsaid"): {
        "risk_level": "HIGH",
        "description": "Potential for increased methotrexate toxicity when combined with NSAIDs.",
        "recommendation": "Avoid unless specifically prescribed; monitor labs.",
    },
    ("digoxin", "amiodarone"): {
        "risk_level": "HIGH",
        "description": "Amiodarone may increase digoxin levels, raising risk of digoxin toxicity.",
        "recommendation": "Monitor digoxin levels and clinical status.",
    },
}


HIGH_KEYWORDS = {
    "fatal",
    "death",
    "hemorrhage",
    "bleeding",
    "seizure",
    "serious",
    "contraindicated",
    "avoid",
    "life-threatening",
}

MODERATE_KEYWORDS = {
    "increase",
    "decrease",
    "reduce",
    "monitor",
    "caution",
    "elevated",
    "risk",
    "affect",
}

LOW_KEYWORDS = {
    "may",
    "possible",
    "mild",
    "slight",
    "minor",
}


def normalize_name(raw: str) -> str:
    """Normalize medicine name to a lookup-friendly generic form."""
    if raw is None:
        return ""

    s = str(raw).strip().lower()

    # Remove dosage like: "Warfarin 5mg", "Paracetamol 650 mg", "Metformin 500mg SR"
    # Keep only leading alphabetic-ish token chunk(s).
    # Strategy: drop trailing numbers/units; also drop anything after the first dosage-like token.
    s = re.sub(r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|g|gm|ml|mL|unit|units|sr|xl|er|od|twice|t[dD]|bd|tid|q[dD]s|qhs)\b.*$", "", s)

    # If still includes patterns like '5mg' without space
    s = re.sub(r"\b\d+(?:\.\d+)?\s*(mg|mcg|g|ml)\b.*$", "", s)

    # Collapse multiple spaces
    s = re.sub(r"\s+", " ", s).strip()

    # Brand mapping: try exact word match on tokens
    # For multi-word brand names, check entire string first.
    if s in BRAND_TO_GENERIC:
        return BRAND_TO_GENERIC[s]

    # Check tokens (e.g., "Crocin 650" -> token 'crocin')
    tokens = [t for t in re.split(r"[^a-z0-9+]+", s) if t]
    for t in tokens:
        if t in BRAND_TO_GENERIC:
            return BRAND_TO_GENERIC[t]

    # Handle Combiflam style (ibuprofen + paracetamol)
    if s.startswith("combiflam") or "combiflam" in s:
        return BRAND_TO_GENERIC["combiflam"]

    return s


def rxnorm_lookup(name: str) -> Optional[str]:
    """Try to resolve a medicine name via RxNorm (RxNav API). Returns a normalized name or None.

    This is a best-effort lookup and may fail due to network or API changes. We keep it optional.
    """
    try:
        term = str(name).strip()
        if not term:
            return None

        # Use approximateTerm to get candidate rxcui
        url = "https://rxnav.nlm.nih.gov/REST/approximateTerm.json"
        resp = requests.get(url, params={"term": term, "maxEntries": 1}, timeout=3)
        resp.raise_for_status()
        data = resp.json()
        group = data.get("approximateGroup") or {}
        candidates = group.get("candidate") or []
        if not candidates:
            return None

        rxcui = candidates[0].get("rxcui")
        if not rxcui:
            return None

        # Fetch properties for the rxcui
        prop_url = f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/properties.json"
        p = requests.get(prop_url, timeout=3)
        p.raise_for_status()
        props = p.json().get("properties") or {}
        name_prop = props.get("name")
        if name_prop:
            return name_prop.strip().lower()
    except Exception:
        return None


def normalize_name(raw: str) -> str:
    """Normalize medicine name to a lookup-friendly generic form.

    Enhanced: try brand map first, then RxNorm, then fallback to token-based heuristics.
    """
    if raw is None:
        return ""

    s = str(raw).strip().lower()

    # Remove dosage like: "Warfarin 5mg", "Paracetamol 650 mg", "Metformin 500mg SR"
    s = re.sub(r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|g|gm|ml|mL|unit|units|sr|xl|er|od|twice|t[dD]|bd|tid|q[dD]s|qhs)\b.*$", "", s)
    s = re.sub(r"\b\d+(?:\.\d+)?\s*(mg|mcg|g|ml)\b.*$", "", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Brand mapping
    if s in BRAND_TO_GENERIC:
        return BRAND_TO_GENERIC[s]

    tokens = [t for t in re.split(r"[^a-z0-9+]+", s) if t]
    for t in tokens:
        if t in BRAND_TO_GENERIC:
            return BRAND_TO_GENERIC[t]

    if s.startswith("combiflam") or "combiflam" in s:
        return BRAND_TO_GENERIC["combiflam"]

    # Try RxNorm lookup (best-effort)
    rx = rxnorm_lookup(s)
    if rx:
        return rx

    return s


def generate_pairs(medicines: List[str]) -> List[Tuple[str, str]]:
    return list(itertools.combinations(medicines, 2))


def _interaction_cache_key(drug_a: str, drug_b: str) -> Tuple[str, str]:
    # Make order deterministic to improve cache hits.
    a, b = sorted([drug_a, drug_b])
    return a, b


def check_local_table(drug_a: str, drug_b: str) -> Optional[Dict[str, str]]:
    a, b = _interaction_cache_key(drug_a, drug_b)
    return KNOWN_INTERACTIONS.get((a, b))


def check_cache(drug_a: str, drug_b: str) -> Optional[Dict[str, Any]]:
    a, b = _interaction_cache_key(drug_a, drug_b)
    db_path = get_db_path()
    if not os.path.exists(db_path):
        return None

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT risk_level, description, checked_at
            FROM interaction_cache
            WHERE drug_a=? AND drug_b=?
            """,
            (a, b),
        )
        row = cur.fetchone()
        if not row:
            return None
        risk_level, description, checked_at = row
        return {
            "risk_level": risk_level,
            "description": description,
            "checked_at": checked_at,
        }
    finally:
        conn.close()


def save_cache(drug_a: str, drug_b: str, risk_level: str, description: str) -> None:
    a, b = _interaction_cache_key(drug_a, drug_b)
    db_path = get_db_path()
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO interaction_cache
            (drug_a, drug_b, risk_level, description, checked_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                a,
                b,
                risk_level,
                description,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def score_severity(text: str) -> str:
    if not text:
        return "NONE"
    t = text.lower()

    if any(k in t for k in HIGH_KEYWORDS):
        return "HIGH"
    if any(k in t for k in MODERATE_KEYWORDS):
        return "MODERATE"
    if any(k in t for k in LOW_KEYWORDS):
        return "LOW"
    return "NONE"


def _fetch_openfda_interactions_text(drug: str, other_drug: str) -> Optional[str]:
    # Search labels by generic name. Parse drug_interactions.
    url = "https://api.fda.gov/drug/label.json"
    params = {
        "search": f"openfda.generic_name:{drug}",
        "limit": 1,
    }

    # 5s timeout as requested
    r = requests.get(url, params=params, timeout=5)
    r.raise_for_status()
    data = r.json()

    results = data.get("results") or []
    if not results:
        return None

    # OpenFDA schema for label.json includes 'drug_interactions'
    # which can be string, or in some cases missing.
    label = results[0]
    interactions = label.get("drug_interactions")
    if not interactions:
        return None

    # Only return the interaction text if it mentions the OTHER drug.
    # Use word-ish match to reduce false positives.
    other = other_drug.lower()
    if other in str(interactions).lower():
        return str(interactions)
    return None


def query_openfda(drug_a: str, drug_b: str) -> Tuple[str, str]:
    """Return (risk_level, description)."""
    # Query both directions; whichever finds text first wins.
    try:
        text = _fetch_openfda_interactions_text(drug_a, drug_b)
        if text is None:
            text = _fetch_openfda_interactions_text(drug_b, drug_a)

        if text is None:
            return "UNKNOWN", "unable to verify from OpenFDA"

        risk = score_severity(text)
        if risk == "NONE":
            return "UNKNOWN", "No relevant interaction keywords found"

        return risk, "OpenFDA reported drug_interactions related to the combination"

    except requests.exceptions.Timeout:
        return "UNKNOWN", "unable to verify (OpenFDA timeout)"
    except Exception:
        return "UNKNOWN", "unable to verify (OpenFDA failure)"


def process_interactions(payload: dict) -> dict:
    medicines_in = payload.get("medicines") if isinstance(payload, dict) else None
    if not medicines_in or not isinstance(medicines_in, list):
        medicines_in = []

    if len(medicines_in) < 2:
        return {
            "pairs_checked": 0,
            "interactions_found": 0,
            "highest_risk": "NONE",
            "interactions": [],
            "safe_pairs": [],
            "message": "Need at least 2 medicines to check interactions",
        }

    # Normalize and dedupe while keeping order
    normalized: List[str] = []
    seen = set()
    for m in medicines_in:
        nm = normalize_name(m)
        if not nm:
            continue
        # Drop empty / duplicates
        if nm not in seen:
            normalized.append(nm)
            seen.add(nm)

    if len(normalized) < 2:
        return {
            "pairs_checked": 0,
            "interactions_found": 0,
            "highest_risk": "NONE",
            "interactions": [],
            "safe_pairs": [],
            "message": "Need at least 2 medicines to check interactions",
        }

    pairs = generate_pairs(normalized)
    interactions: List[Dict[str, str]] = []
    safe_pairs: List[Dict[str, str]] = []

    highest_risk_rank = 0  # 0 NONE/UNKNOWN, 1 LOW, 2 MODERATE, 3 HIGH

    def rank(r: str) -> int:
        return {"NONE": 0, "UNKNOWN": 0, "LOW": 1, "MODERATE": 2, "HIGH": 3}.get(r, 0)

    for a, b in pairs:
        # Skip if normalized is exactly the same
        if a == b:
            continue

        cached = check_cache(a, b)
        if cached:
            risk_level = cached["risk_level"]
            description = cached["description"]
        else:
            local = check_local_table(a, b)
            if local:
                risk_level = local["risk_level"]
                description = local["description"]
            else:
                risk_level, description = query_openfda(a, b)

            save_cache(a, b, risk_level, description)

        if risk_level in ("HIGH", "MODERATE", "LOW"):
            rec = "Consult doctor before taking together."  # default fallback
            local = check_local_table(a, b)
            if local:
                rec = local.get("recommendation", rec)
            else:
                # OpenFDA fallback: generic recommendation
                rec = "Consult doctor before taking together."

            interactions.append(
                {
                    "drug_a": a,
                    "drug_b": b,
                    "risk_level": risk_level,
                    "description": description,
                    "recommendation": rec,
                    "source": "OpenFDA" if local is None else "OpenFDA",
                }
            )
            highest_risk_rank = max(highest_risk_rank, rank(risk_level))
        else:
            safe_pairs.append({"drug_a": a, "drug_b": b})

    highest = {0: "NONE", 1: "LOW", 2: "MODERATE", 3: "HIGH"}[highest_risk_rank]

    return {
        "pairs_checked": len(pairs),
        "interactions_found": len(interactions),
        "highest_risk": highest,
        "interactions": interactions,
        "safe_pairs": safe_pairs,
    }


def get_known_interactions() -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for (a, b), v in KNOWN_INTERACTIONS.items():
        rows.append(
            {
                "drug_a": a,
                "drug_b": b,
                "risk_level": v.get("risk_level", "HIGH"),
                "description": v.get("description", ""),
                "recommendation": v.get("recommendation", ""),
                "source": "OpenFDA",
            }
        )
    return rows


