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

        # Seed pharmacy tables
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pharmacy (
                pharmacy_id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                address TEXT NOT NULL,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                city TEXT
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS pharmacy_inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pharmacy_id INTEGER NOT NULL,
                medicine_name TEXT NOT NULL,
                price_inr INTEGER NOT NULL,
                in_stock INTEGER DEFAULT 1,
                FOREIGN KEY (pharmacy_id) REFERENCES pharmacy(pharmacy_id),
                UNIQUE(pharmacy_id, medicine_name)
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS price_alerts (
                user_id TEXT NOT NULL,
                medicine TEXT NOT NULL,
                target_price REAL NOT NULL,
                current_price REAL NOT NULL,
                notify_on_any_drop INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, medicine)
            );
            """
        )

        # Ensure schema compatibility for existing databases
        cur.execute("PRAGMA table_info(price_alerts)")
        columns = [row[1] for row in cur.fetchall()]
        if "updated_at" not in columns:
            cur.execute("ALTER TABLE price_alerts ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS price_history (
                medicine TEXT NOT NULL,
                price REAL NOT NULL,
                recorded_at TIMESTAMP NOT NULL,
                source TEXT NOT NULL
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS alert_log (
                user_id TEXT NOT NULL,
                medicine TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                old_price REAL NOT NULL,
                new_price REAL NOT NULL,
                triggered_at TIMESTAMP NOT NULL,
                was_seen INTEGER NOT NULL DEFAULT 0
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS search_frequency (
                medicine TEXT NOT NULL,
                location TEXT,
                search_count INTEGER NOT NULL DEFAULT 1,
                recorded_date DATE NOT NULL
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS shortage_reports (
                medicine TEXT NOT NULL,
                location TEXT NOT NULL,
                reported_by TEXT NOT NULL,
                pharmacy_name TEXT,
                confirmed INTEGER NOT NULL DEFAULT 0,
                reported_at TIMESTAMP NOT NULL
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS shortage_watchlist (
                medicine TEXT PRIMARY KEY,
                shortage_risk TEXT NOT NULL,
                risk_score INTEGER NOT NULL,
                last_updated TIMESTAMP NOT NULL,
                seasonal_flag INTEGER NOT NULL DEFAULT 0
            );
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS seasonal_patterns (
                medicine TEXT PRIMARY KEY,
                peak_months TEXT NOT NULL,
                reason TEXT,
                demand_increase_pct INTEGER NOT NULL DEFAULT 0
            );
            """
        )

        # OCR / prescription storage
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS prescriptions (
                prescription_id TEXT PRIMARY KEY,
                user_id TEXT,
                raw_text TEXT,
                medicines_json TEXT,
                image_path TEXT,
                confidence TEXT,
                extracted_at TIMESTAMP
            );
            """
        )

        conn.commit()
        cur.execute("SELECT COUNT(1) FROM pharmacy")
        row = cur.fetchone()
        if row and row[0] == 0:
            from backend.pharmacy import PHARMACY_DATA, MEDICINE_BASE_PRICES, get_pharmacy_price_variant

            # Insert pharmacies
            for pharmacy in PHARMACY_DATA:
                cur.execute(
                    "INSERT INTO pharmacy (name, address, lat, lng, city) VALUES (?, ?, ?, ?, ?)",
                    (pharmacy["name"], pharmacy["address"], pharmacy["lat"], pharmacy["lng"], "Bengaluru")
                )
            conn.commit()

            # Get pharmacy IDs
            cur.execute("SELECT pharmacy_id FROM pharmacy")
            pharmacy_ids = [row[0] for row in cur.fetchall()]

            # Insert inventory for each pharmacy
            for pharmacy_id in pharmacy_ids:
                for medicine_name, base_price in MEDICINE_BASE_PRICES.items():
                    varied_price = get_pharmacy_price_variant(base_price, pharmacy_id)
                    # 90% of pharmacies stock each medicine (deterministic: based on pharmacy_id)
                    in_stock = 1 if (pharmacy_id % 10 != 9) else 0
                    try:
                        cur.execute(
                            "INSERT INTO pharmacy_inventory (pharmacy_id, medicine_name, price_inr, in_stock) VALUES (?, ?, ?, ?)",
                            (pharmacy_id, medicine_name, varied_price, in_stock)
                        )
                    except sqlite3.IntegrityError:
                        pass
            conn.commit()

    finally:
        conn.close()


