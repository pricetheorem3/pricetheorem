# db.py – SQLite helper for iGOT dashboard
# ────────────────────────────────────────
import sqlite3

DB_FILE = "alerts.db"

def init_db():
    """
    Create the alerts table if it doesn't exist.
    """
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol       TEXT,
            time         TEXT,
            move         TEXT,
            ltp          REAL,

            dce          REAL,
            dpe          REAL,
            skew         REAL,
            doi_put      INTEGER,
            call_vol     REAL,

            trend        TEXT,
            flag         TEXT,
            ivd_ce       REAL,
            ivd_pe       REAL,
            iv_flag      TEXT,
            signal       TEXT,        -- NEW (trend + IV flag)

            call_result  TEXT,
            put_result   TEXT
        )
        """)
