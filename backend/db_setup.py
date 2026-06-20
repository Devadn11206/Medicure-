import os
import sqlite3
from typing import Optional


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
        conn.commit()
    finally:
        conn.close()


