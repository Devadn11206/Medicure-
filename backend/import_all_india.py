"""
Importer for All India Drug Bank CSV into local sqlite DB used by the app.
Usage:
  python backend/import_all_india.py backend/all_india_drug_bank.csv

This script is defensive: it normalizes names, deduplicates inserts, and records provenance.
"""
import csv
import os
import sqlite3
import sys
import re
from datetime import datetime, timezone
from typing import List

from .db_setup import get_db_path, init_db
from .interactions import normalize_name

# Fast local normalization used for bulk import to avoid per-row network calls to RxNorm/OpenFDA
def normalize_local(raw: str) -> str:
    if raw is None:
        return ""
    s = str(raw).strip().lower()
    s = re.sub(r"\b(\d+(?:\.\d+)?)\s*(mg|mcg|g|gm|ml|mL|unit|units|sr|xl|er|od|twice|t[dD]|bd|tid|q[dD]s|qhs)\b.*$", "", s)
    s = re.sub(r"\b\d+(?:\.\d+)?\s*(mg|mcg|g|ml)\b.*$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    try:
        from .interactions import BRAND_TO_GENERIC
        if s in BRAND_TO_GENERIC:
            return BRAND_TO_GENERIC[s]
        tokens = [t for t in re.split(r"[^a-z0-9+]+", s) if t]
        for t in tokens:
            if t in BRAND_TO_GENERIC:
                return BRAND_TO_GENERIC[t]
    except Exception:
        pass
    return s


def iter_substitutes(row: dict) -> List[str]:
    out = []
    for i in range(5):
        k = f"substitute{i}"
        v = row.get(k)
        if v:
            v = v.strip()
            if v:
                out.append(v)
    return out


def iter_side_effects(row: dict) -> List[str]:
    out = []
    for i in range(42):
        k = f"sideEffect{i}"
        v = row.get(k)
        if v:
            v = v.strip()
            if v:
                out.append(v)
    return out


def iter_uses(row: dict) -> List[str]:
    out = []
    for i in range(5):
        k = f"use{i}"
        v = row.get(k)
        if v:
            v = v.strip()
            if v:
                out.append(v)
    return out


def import_file(csv_path: str) -> None:
    if not os.path.exists(csv_path):
        print(f"CSV file not found: {csv_path}")
        return

    db_path = get_db_path()
    init_db(db_path)

    # Use a longer timeout and enable WAL mode to reduce 'database is locked' errors
    conn = sqlite3.connect(db_path, timeout=60)
    cur = conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL;")
    except Exception:
        pass

    imported = 0
    skipped = 0

    with open(csv_path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            src_id = (row.get("id") or "").strip()
            name = (row.get("name") or "").strip()
            if not name:
                skipped += 1
                continue

            canonical = normalize_local(name)
            chemical_class = row.get("Chemical Class") or row.get("chemical class") or None
            habit = row.get("Habit Forming") or None
            therapeutic = row.get("Therapeutic Class") or row.get("therapeutic class") or None
            action = row.get("Action Class") or row.get("action class") or None

            imported_at = datetime.now(timezone.utc).isoformat()

            # Upsert into drugs
            try:
                cur.execute(
                    "INSERT OR REPLACE INTO drugs (id, name, canonical_name, chemical_class, habit_forming, therapeutic_class, action_class, source, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        src_id or None,
                        name,
                        canonical,
                        chemical_class,
                        habit,
                        therapeutic,
                        action,
                        "all_india_drug_bank",
                        imported_at,
                    ),
                )
            except Exception as e:
                print("Failed to insert drug:", name, e)
                skipped += 1
                continue

            drug_id = src_id or name

            # Aliases / substitutes
            for alias in iter_substitutes(row):
                try:
                    cur.execute(
                        "INSERT OR IGNORE INTO drug_aliases (drug_id, alias) VALUES (?, ?)",
                        (drug_id, alias),
                    )
                except Exception:
                    pass

            # Side effects
            for se in iter_side_effects(row):
                try:
                    cur.execute(
                        "INSERT OR IGNORE INTO drug_side_effects (drug_id, side_effect) VALUES (?, ?)",
                        (drug_id, se),
                    )
                except Exception:
                    pass

            # Uses
            for u in iter_uses(row):
                try:
                    cur.execute(
                        "INSERT OR IGNORE INTO drug_uses (drug_id, use_case) VALUES (?, ?)",
                        (drug_id, u),
                    )
                except Exception:
                    pass

            # Add brand->canonical mapping to in-memory mapping (optional): we could write a small file, but for now we update BRAND_TO_GENERIC if available
            try:
                from .interactions import BRAND_TO_GENERIC

                key = name.strip().lower()
                if key and key not in BRAND_TO_GENERIC:
                    BRAND_TO_GENERIC[key] = canonical
            except Exception:
                pass

            imported += 1

    conn.commit()
    conn.close()

    # Persist in-repo brand->generic mapping for future runs
    try:
        from .interactions import BRAND_TO_GENERIC
        out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brand_to_generic.json")
        import json
        with open(out_path, "w", encoding="utf-8") as of:
            json.dump(BRAND_TO_GENERIC, of, ensure_ascii=False, indent=2)
    except Exception:
        pass

    print(f"Import complete. Imported: {imported}, Skipped: {skipped}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python backend/import_all_india.py path/to/all_india_drug_bank.csv")
        sys.exit(1)
    import_file(sys.argv[1])
