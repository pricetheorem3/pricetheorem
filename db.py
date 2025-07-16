# db.py  –  SQLite helper for iGOT dashboard
# ───────────────────────────────────────────
import sqlite3

# The SQLite database file will live in the project root
DB_FILE = "alerts.db"

def init_db():
    """
    Create the alerts table if it doesn't exist.
    Run once at app startup (called from app.py).
    """
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol       TEXT,
            time         TEXT,
            move         TEXT,
            ltp          REAL,

            dce          REAL,        -- ΔCE  (premium change on calls)
            dpe          REAL,        -- ΔPE  (premium change on puts)
            skew         REAL,        -- IV skew  = IV(CE) - IV(PE)
            doi_put      INTEGER,     -- ΔOI on puts (live - baseline)

            call_vol     REAL,        -- 5‑min volume ratio ×1000
            trend        TEXT,        -- Bullish / Bearish / Flat
            flag         TEXT,        -- Flat PE / Strong CE / ""

            ivd_ce       REAL,        -- IVΔ for ATM CE (today - 9:15)
            ivd_pe       REAL,        -- IVΔ for ATM PE
            iv_flag      TEXT,        -- IV Pump / IV Crush / ""

            call_result  TEXT,        -- ✅ / ❌ (volume spike direction CE)
            put_result   TEXT         -- ✅ / ❌ (volume spike direction PE)
        )
        """)
