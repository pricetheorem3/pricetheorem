# app.py – option‑chain screener with CE/PE premium‑change, option‑volume checks, Telegram alerts
# ───────────────────────────────────────────────────────────
"""
This unified version merges **your existing Flask/Kite/Telegram app** with the
new **ΔCE / ΔPE (premium‑decay) logic** that mimics icharts.in:

• `compute_ce_pe_change()` → sums (LTP‑Open) for CE & PE across ATM±1 strikes.
• Adds `ce_chg`, `pe_chg` to every alert row (ready for the two new columns in
  *index.html*).
• Keeps all previous features: login page, option‑volume checks, Telegram
  notifications, SQLite‑style JSON storage, Render‑friendly env‑vars.

Only *index.html* needs two extra columns to show the new fields.
"""

import os, json, math, datetime, logging, pathlib, requests
from flask import Flask, request, render_template, redirect, url_for, session
from kiteconnect import KiteConnect

# ─── Time‑zone helpers ─────────────────────────────────────
try:
    from zoneinfo import ZoneInfo          # Python ≥ 3.9
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:                         # pragma: no cover (Py < 3.9)
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

UTC = datetime.timezone.utc

# ─── Global constants ─────────────────────────────────────
WIDTH          = 2      # ATM ± 2 strikes for volume‑check logic (unchanged)
WIDTH_CE_PE    = 1      # ATM ± 1 for premium‑decay (icharts style)
STRIKE_STEP    = 10     # default strike step for equities (₹10). <₹500 → ₹5.

# ─── File paths (Render‑disk safe) ─────────────────────────
DATA_DIR    = pathlib.Path(os.getenv("DATA_DIR", "."))
ALERTS_FILE = DATA_DIR / "alerts.json"
TOKEN_FILE  = DATA_DIR / "access_token.txt"

# ─── Flask & env‑vars ─────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "changeme")

KITE_API_KEY    = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")

# Telegram (must exist in environment)
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")      # 1234…:AA…
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")    # 9876…
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError("Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in environment")

# ─── Telegram helper ───────────────────────────────────────

def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, data=payload, timeout=5)
        if r.status_code != 200:
            logging.error("Telegram error: %s", r.text)
        return r.status_code == 200
    except Exception as e:
        logging.exception("Telegram exception")
        return False

# ─── Kite helpers & instrument cache ───────────────────────

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

# ─── Strike‑step & option‑symbol helpers (for ΔCE/ΔPE) ────

def get_strike_step(price: float) -> int:
    """₹5 steps below 500; else default STRIKE_STEP (₹10)."""
    return 5 if price < 500 else STRIKE_STEP


def format_option_symbol(sym: str, expiry: str, strike: int, kind: str) -> str:
    """Return tradingsymbol like RELIANCE25JUL1480CE/PE."""
    return f"{sym}{expiry}{strike}{kind}"


def ltp_and_open(kite: KiteConnect, symbols: list[str]) -> dict[str, tuple[float, float]]:
    """Bulk fetch LTP & OPEN via kite.quote()."""
    quotes = kite.quote(symbols)
    return {s: (d["last_price"], d["ohlc"]["open"]) for s, d in quotes.items()}

# ─── ΔCE / ΔPE calculation ─────────────────────────────────

def compute_ce_pe_change(kite: KiteConnect, symbol: str, width: int = WIDTH_CE_PE):
    """Return (ΔCE, ΔPE) across ATM±width strikes (icharts Premium Decay)."""
    base = symbol.replace("NSE:", "").upper()
    spot = kite.ltp([f"NSE:{base}"])[f"NSE:{base}"]["last_price"]
    step = get_strike_step(spot)
    atm  = round(spot / step) * step

    today = datetime.datetime.now(IST).date()
    # crude monthly code – last Thursday of current month
    last_thu = today + datetime.timedelta((3 - today.weekday()) % 7)
    expiry_code = last_thu.strftime("%d%b").upper()   # e.g. 25JUL

    strikes, symbols = [], []
    for off in range(-width, width + 1):
        st = atm + off * step
        strikes.append(st)
        symbols += [
            format_option_symbol(base, expiry_code, st, "CE"),
            format_option_symbol(base, expiry_code, st, "PE"),
        ]

    data = ltp_and_open(kite, symbols)
    d_ce = d_pe = 0.0
    for st in strikes:
        ce_sym = format_option_symbol(base, expiry_code, st, "CE")
        pe_sym = format_option_symbol(base, expiry_code, st, "PE")
        ce_ltp, ce_open = data[ce_sym]
        pe_ltp, pe_open = data[pe_sym]
        d_ce += ce_ltp - ce_open
        d_pe += pe_ltp - pe_open

    return round(d_ce, 2), round(d_pe, 2)

# ─── Misc option‑chain helpers (from original code) ───────

def token_for_symbol(tsym: str):
    for inst in get_instruments():
        if inst["tradingsymbol"] == tsym:
            return inst["instrument_token"]
    return None


def next_expiry(symbol: str):
    s, today = symbol.upper(), datetime.datetime.now(IST).date()
    dates = sorted({i["expiry"] for i in get_instruments()
                    if i["instrument_type"] in {"PE", "CE"} and
                       (i["name"] == s or i["tradingsymbol"].startswith(s))})
    for d in dates:
        if d >= today:
            return d
    return dates[-1]


def strikes_from_chain(sym, exp_str, spot):
    exp = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
    matches = [i for i in get_instruments()
               if i["instrument_type"] in {"PE", "CE"} and
                  i["expiry"] == exp and
                  (i["name"] == sym.upper() or i["tradingsymbol"].startswith(sym.upper()))]
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
                (i["name"] == sym.upper() or i["tradingsymbol"].startswith(sym.upper()))):
            return i["tradingsymbol"]
    return None

# ─── 5‑minute candle high‑volume rule (unchanged) ─────────

def check_option(tsym, is_put):
    token = token_for_symbol(tsym)
    if token is None:
        logging.warning("No instrument_token for %s", tsym)
        return "❌"
    kite = get_kite()
    end = datetime.datetime.now(IST)
    start = datetime.datetime.combine(end.date(), datetime.time(9, 15, tzinfo=IST))
    try:
        cds = kite.historical_data(token, start, end, "5minute")
        if not cds:
            return "❌"
        latest = cds[-1]
        if latest["volume"] != max(c["volume"] for c in cds):
            return "❌"
        green = latest["close"] > latest["open"]
        red   = latest["close"] < latest["open"]
        return "✅" if ((is_put and green) or (not is_put and red)) else "❌"
    except Exception:
        logging.exception("check_option error")
        return "❌"

# ─── Alert persistence (JSON) ─────────────────────────────

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
        hist = []
        if ALERTS_FILE.exists():
            hist = json.loads(ALERTS_FILE.read_text())
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
    return render_template("index.html", alerts=alerts, kite_api_key=KITE_API_KEY)


@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        if (request.form.get("username") == os.getenv("APP_USERNAME", "admin") and
                request.form.get("password") == os.getenv("APP_PASSWORD", "price123")):
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
    if not rt:
        return "No request_token", 400
    try:
        kite = KiteConnect(api_key=KITE_API_KEY)
        data = kite.generate_session(rt, api_secret=KITE_API_SECRET)
        TOKEN_FILE.write_text(data["access_token"])
        logging.info("Access token saved")
        return redirect(url_for("index"))
    except Exception:
        logging.exception("Token generation failed")
        return "Token generation failed", 500

# ─── Webhook core ─────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    p = request.json or {}
    symbol = p.get("symbol")
    if not symbol:
        return "Missing symbol", 400

    # trigger time parsing (robust)
    trg = p.get("trigger_time")
    if trg:
        try:
            trig_dt = datetime.datetime.fromtimestamp(int(trg), UTC).astimezone(IST)
        except (ValueError, TypeError):
            try:
                iso_dt = datetime.datetime.fromisoformat(trg.rstrip("Z"))
                trig_dt = (iso_dt if iso_dt.tzinfo else iso_dt.replace(tzinfo=UTC)).astimezone(IST)
            except Exception:
                trig_dt = datetime.datetime.now(IST)
    else:
        trig_dt = datetime.datetime.now(IST)

    kite = get_kite()
    try:
        # ΔCE / ΔPE premium‑decay
        delta_ce, delta_pe = compute_ce_pe_change(kite, symbol)

        # underlying LTP & move
        ltp = kite.ltp([f"NSE:{symbol.upper()}"])[f"NSE:{symbol.upper()}"]["last_price"]
        prev_close = kite.quote([f"NSE:{symbol.upper()}"])[f"NSE:{symbol.upper()}"]["ohlc"]["close"]
        move_pct = round((ltp - prev_close) / prev_close * 100, 2)

        # option‑volume logic (unchanged)
        expiry = next_expiry(symbol).strftime("%Y-%m-%d")
        strikes = strikes_from_chain(symbol, expiry, ltp)
        if not strikes:
            put_result = call_result = "No option chain"
        else:
            put_tags, call_tags = [], []
            for st in strikes:
                pe = option_symbol(symbol, expiry, st, "PUT")
                ce = option_symbol(symbol, expiry, st, "CALL")
                put_tags.append(f"{st}{check_option(pe, True) if pe else '❌'}")
                call_tags.append(f"{st}{check_option(ce, False) if ce else '❌'}")
            put_result  = "  ".join(put_tags)
            call_result = "  ".join(call_tags)

        alert = {
            "symbol": symbol.upper(),
            "time": trig_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "ltp": f"₹{ltp:.2f}",
            "move": move_pct,
            "ce_chg": delta_ce,
            "pe_chg": delta_pe,
            "put_result": put_result,
            "call_result": call_result,
        }
        save_alert(alert)

        # Telegram push (only if ✅ appears)
        if "✅" in put_result or "✅" in call_result:
            msg = (
                f"*New Signal* 📊\n"
                f"Symbol: `{alert['symbol']}`\n"
                f"Time  : {alert['time']}\n"
                f"LTP   : {alert['ltp']}\n"
                f"ΔCE   : {delta_ce:+}  |  ΔPE: {delta_pe:+}\n"
                f"PUT   : {put_result}\n"
                f"CALL  : {call_result}"
            )
            send_telegram(msg)

        return "OK", 200
    except Exception:
        logging.exception("Webhook error")
        return "Error", 500

# ─── Dev runner ───────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)

