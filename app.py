# app.py – option‑chain screener with CE/PE premium‑change (chain‑safe),
#          option‑volume checks, Telegram alerts
# ───────────────────────────────────────────────────────────
"""
FINAL unified version
─────────────────────
• Premium‑decay calc uses strikes_from_chain() *and* requests quotes with an
  “NFO:” prefix → no KeyError and ΔCE/ΔPE values no longer stay at 0.00.
• All earlier functionality (login, volume‑spike ✅/❌, Telegram alerts, JSON
  storage) is unchanged.
"""

import os, json, datetime, logging, pathlib, requests
from flask import Flask, request, render_template, redirect, url_for, session
from kiteconnect import KiteConnect

# ─── Time‑zone helpers ─────────────────────────────────────
try:
    from zoneinfo import ZoneInfo           # Py ≥3.9
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:                         # Py <3.9
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

UTC = datetime.timezone.utc

# ─── Constants ────────────────────────────────────────────
WIDTH          = 2   # ATM ±2 strikes for volume‑spike logic
WIDTH_CE_PE    = 1   # ATM ±1 for premium‑decay
STRIKE_STEP    = 10  # default step; below ₹500 use 5

# ─── Paths ────────────────────────────────────────────────
DATA_DIR    = pathlib.Path(os.getenv("DATA_DIR", "."))
ALERTS_FILE = DATA_DIR / "alerts.json"
TOKEN_FILE  = DATA_DIR / "access_token.txt"

# ─── Flask / env vars ─────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "changeme")

KITE_API_KEY    = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError("Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID env vars")

# ─── Telegram helper ─────────────────────────────────────
def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text,
               "parse_mode": "Markdown"}
    try:
        r = requests.post(url, data=payload, timeout=5)
        if r.status_code != 200:
            logging.error("Telegram error: %s", r.text)
        return r.status_code == 200
    except Exception:
        logging.exception("Telegram exception")
        return False

# ─── Kite helpers & instrument cache ─────────────────────
def get_kite():
    kite = KiteConnect(api_key=KITE_API_KEY)
    if TOKEN_FILE.exists():
        kite.set_access_token(TOKEN_FILE.read_text().strip())
    return kite

INSTRUMENTS, INSTR_DATE = None, None
def get_instruments():
    global INSTRUMENTS, INSTR_DATE
    today = datetime.datetime.now(IST).date()
    if INSTRUMENTS is None or INSTR_DATE != today:
        INSTRUMENTS = get_kite().instruments("NFO")
        INSTR_DATE  = today
    return INSTRUMENTS

# ─── Utility helpers ─────────────────────────────────────
def get_strike_step(price: float) -> int:
    return 5 if price < 500 else STRIKE_STEP

def format_option_symbol(sym: str, expiry_code: str,
                         strike: int, kind: str) -> str:
    return f"{sym}{expiry_code}{strike}{kind}"

def ltp_and_open(kite: KiteConnect, symbols: list[str]):
    q = kite.quote(symbols)
    return {s: (d["last_price"], d["ohlc"]["open"]) for s, d in q.items()}

# ─── Option‑chain helpers ─────────────────────────────────
def next_expiry(symbol: str):
    s, today = symbol.upper(), datetime.datetime.now(IST).date()
    dates = sorted({i["expiry"] for i in get_instruments()
                    if i["instrument_type"] in {"PE", "CE"} and
                       (i["name"] == s or
                        i["tradingsymbol"].startswith(s))})
    for d in dates:
        if d >= today:
            return d
    return dates[-1]

def strikes_from_chain(sym, exp_str, spot):
    exp = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
    matches = [i for i in get_instruments()
               if i["instrument_type"] in {"PE", "CE"} and
                  i["expiry"] == exp and
                  (i["name"] == sym.upper() or
                   i["tradingsymbol"].startswith(sym.upper()))]
    if not matches:
        return []
    strikes = sorted({i["strike"] for i in matches})
    atm = min(strikes, key=lambda s: abs(s - spot))
    i = strikes.index(atm)
    return strikes[max(0, i - WIDTH): i + WIDTH + 1]

def option_symbol(sym, exp_str, strike, kind):
    exp = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
    for i in get_instruments():
        if (i["instrument_type"] == ("PE" if kind == "PUT" else "CE") and
            i["strike"] == strike and i["expiry"] == exp and
            (i["name"] == sym.upper() or
             i["tradingsymbol"].startswith(sym.upper()))):
            return i["tradingsymbol"]
    return None

# ─── Premium‑decay calc (NFO‑prefixed) ───────────────────
def compute_ce_pe_change(kite: KiteConnect, symbol: str,
                         width: int = WIDTH_CE_PE):
    base = symbol.replace("NSE:", "").upper()
    spot = kite.ltp([f"NSE:{base}"])[f"NSE:{base}"]["last_price"]

    expiry_dt  = next_expiry(base)
    expiry_str = expiry_dt.strftime("%Y-%m-%d")
    strikes    = strikes_from_chain(base, expiry_str, spot)
    if not strikes:
        return 0.0, 0.0

    expiry_code = expiry_dt.strftime("%d%b").upper()

    # Build NFO‑prefixed symbols
    prefixed = []
    for st in strikes[: width * 2 + 1]:
        prefixed += [f"NFO:{format_option_symbol(base, expiry_code, st, 'CE')}",
                     f"NFO:{format_option_symbol(base, expiry_code, st, 'PE')}"]

    raw   = kite.quote(prefixed)
    data  = {k.split(':')[1]: (v["last_price"], v["ohlc"]["open"])
             for k, v in raw.items()}

    d_ce = d_pe = 0.0
    for st in strikes:
        ce = format_option_symbol(base, expiry_code, st, "CE")
        pe = format_option_symbol(base, expiry_code, st, "PE")
        if ce in data:
            ce_ltp, ce_open = data[ce]; d_ce += ce_ltp - ce_open
        if pe in data:
            pe_ltp, pe_open = data[pe]; d_pe += pe_ltp - pe_open
    return round(d_ce, 2), round(d_pe, 2)

# ─── 5‑minute volume‑spike check (unchanged) ─────────────
def check_option(tsym, is_put):
    token = next((i["instrument_token"] for i in get_instruments()
                  if i["tradingsymbol"] == tsym), None)
    if token is None: return "❌"
    kite = get_kite()
    end   = datetime.datetime.now(IST)
    start = datetime.datetime.combine(end.date(),
                                      datetime.time(9, 15, tzinfo=IST))
    try:
        cds = kite.historical_data(token, start, end, "5minute")
        if not cds: return "❌"
        latest = cds[-1]
        if latest["volume"] != max(c["volume"] for c in cds):
            return "❌"
        green = latest["close"] > latest["open"]
        red   = latest["close"] < latest["open"]
        return "✅" if ((is_put and green) or
                        (not is_put and red)) else "❌"
    except Exception:
        logging.exception("check_option error")
        return "❌"

# ─── Alert persistence (JSON) ────────────────────────────
def today_str():
    return datetime.datetime.now(IST).strftime("%Y-%m-%d")

alerts = []
if ALERTS_FILE.exists():
    try:
        hist = json.loads(ALERTS_FILE.read_text())
        alerts = [a for a in hist if a["time"].startswith(today_str())]
    except Exception:
        logging.exception("Load alerts error")

def save_alert(a):
    try:
        hist = json.loads(ALERTS_FILE.read_text()) if ALERTS_FILE.exists() else []
        hist = [x for x in hist if x["time"].startswith(today_str())]
        hist.append(a)
        ALERTS_FILE.write_text(json.dumps(hist, indent=2))
        alerts.append(a)
    except Exception:
        logging.exception("Save alert error")

# ─── Routes ───────────────────────────────────────────────
@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login_page"))
    return render_template("index.html",
                           alerts=alerts,
                           kite_api_key=KITE_API_KEY)

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        if (request.form.get("username") ==
                os.getenv("APP_USERNAME", "admin") and
            request.form.get("password") ==
                os.getenv("APP_PASSWORD", "price123")):
            session["logged_in"] = True
            return redirect(url_for("index"))
        return render_template("login.html", error="Invalid credentials")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

@app.route("/login/callback")
def login_callback():
    rt = request.args.get("request_token")
    if not rt: return "No request_token", 400
    try:
        kite = KiteConnect(api_key=KITE_API_KEY)
        data = kite.generate_session(rt, api_secret=KITE_API_SECRET)
        TOKEN_FILE.write_text(data["access_token"])
        logging.info("Access token saved")
        return redirect(url_for("index"))
    except Exception:
        logging.exception("Token generation failed")
        return "Token generation failed", 500

# ─── Webhook ──────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    p = request.json or {}
    symbol = p.get("symbol")
    if not symbol: return "Missing symbol", 400

    trg = p.get("trigger_time")
    if trg:
        try:    trig_dt = datetime.datetime.fromtimestamp(int(trg), UTC).astimezone(IST)
        except (ValueError, TypeError):
            try:
                iso_dt = datetime.datetime.fromisoformat(trg.rstrip("Z"))
                trig_dt = (iso_dt if iso_dt.tzinfo else
                           iso_dt.replace(tzinfo=UTC)).astimezone(IST)
            except Exception:
                trig_dt = datetime.datetime.now(IST)
    else:
        trig_dt = datetime.datetime.now(IST)

    kite = get_kite()
    try:
        d_ce, d_pe = compute_ce_pe
