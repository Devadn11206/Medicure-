import os
import sqlite3
import json
from typing import Optional
from datetime import datetime, timezone


def get_db_path(db_filename: str = "medicines.db") -> str:
    # Store DB inside backend/ so it stays with the project.
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, db_filename)


def init_db(db_path: Optional[str] = None) -> None:
    """Create required tables on startup (idempotent)."""
    if db_path is None:
        db_path = get_db_path()

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        # Cache drug-drug interaction lookups.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS interaction_cache (
                drug_a TEXT NOT NULL,
                drug_b TEXT NOT NULL,
                risk_level TEXT NOT NULL,
                description TEXT NOT NULL,
                checked_at TIMESTAMP NOT NULL,
                PRIMARY KEY (drug_a, drug_b)
            );
            """
        )
        # Tables to store imported drug metadata
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS drugs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                canonical_name TEXT,
                chemical_class TEXT,
                habit_forming TEXT,
                therapeutic_class TEXT,
                action_class TEXT,
                source TEXT,
                imported_at TIMESTAMP
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS drug_aliases (
                drug_id TEXT NOT NULL,
                alias TEXT NOT NULL,
                PRIMARY KEY (drug_id, alias)
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS drug_side_effects (
                drug_id TEXT NOT NULL,
                side_effect TEXT NOT NULL,
                PRIMARY KEY (drug_id, side_effect)
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS drug_uses (
                drug_id TEXT NOT NULL,
                use_case TEXT NOT NULL,
                PRIMARY KEY (drug_id, use_case)
            );
            """
        )
        conn.commit()

        # Seed the table with curated interactions if it's empty.
        cur.execute("SELECT COUNT(1) FROM interaction_cache")
        row = cur.fetchone()
        count = row[0] if row else 0
        if count == 0:
            seed_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seed_interactions.json")
            if os.path.exists(seed_path):
                try:
                    with open(seed_path, "r", encoding="utf-8") as f:
                        items = json.load(f)
                    for it in items:
                        a = it.get("drug_a")
                        b = it.get("drug_b")
                        risk = it.get("risk_level", "UNKNOWN")
                        desc = it.get("description", "")
                        checked_at = datetime.now(timezone.utc).isoformat()
                        if a and b:
                            cur.execute(
                                "INSERT OR REPLACE INTO interaction_cache (drug_a, drug_b, risk_level, description, checked_at) VALUES (?, ?, ?, ?, ?)",
                                (a, b, risk, desc, checked_at),
                            )
                    conn.commit()
                except Exception:
                    # If seeding fails, silently continue; system will still work using OpenFDA lookups.
                    pass

    finally:
        conn.close()


