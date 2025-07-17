# app.py – option-chain screener with CE/PE premium-change (chain-safe + NFO),
#          volume-spike checks, Telegram alerts
# ───────────────────────────────────────────────────────────
"""
FINAL build – 17 Jul 2025

Key points
──────────
• CE/PE premium-decay fetches tradingsymbols directly from the option-chain,
  so it works for weekly & monthly expiries alike.
• ltp_open_map() batches quotes and never raises.
• All prior functionality (login, webhook, Telegram, JSON storage) retained.
• Toggle verbose logs via LOG_LEVEL=INFO.
"""

import os, json, datetime, logging, pathlib, requests, itertools
from flask import Flask, request, render_template, redirect, url_for, session
from kiteconnect import KiteConnect

# ─── Logging ──────────────────────────────────────────────
logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"))

# ─── Time-zone helpers ───────────────────────────────────
try:
    from zoneinfo import ZoneInfo         # Py ≥ 3.9
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:                       # Py < 3.9
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

UTC = datetime.timezone.utc

# ─── Constants ───────────────────────────────────────────
WIDTH_VOL   = 2               # ATM ±2 strikes for volume check
WIDTH_DECAY = 1               # ATM ±1 for premium-decay
QUOTE_BATCH = 25              # max symbols / kite.quote call

# ─── Paths ───────────────────────────────────────────────
DATA_DIR    = pathlib.Path(os.getenv("DATA_DIR", "."))
ALERTS_FILE = DATA_DIR / "alerts.json"
TOKEN_FILE  = DATA_DIR / "access_token.txt"

# ─── Flask & env-vars ────────────────────────────────────
app = Flask(__name__)
app.secret_key   = os.getenv("FLASK_SECRET_KEY", "changeme")
KITE_API_KEY     = os.getenv("KITE_API_KEY")
KITE_API_SECRET  = os.getenv("KITE_API_SECRET")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
    raise RuntimeError("Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID")

# ─── Telegram helper ─────────────────────────────────────
def send_telegram(msg: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception:
        logging.warning("Telegram send failed")

# ─── Kite session & instrument cache ─────────────────────
def kite_session() -> KiteConnect:
    kite = KiteConnect(api_key=KITE_API_KEY)
    if TOKEN_FILE.exists():
        kite.set_access_token(TOKEN_FILE.read_text().strip())
    return kite

_INSTR_CACHE, _CACHE_DATE = None, None
def instruments():
    global _INSTR_CACHE, _CACHE_DATE
    today = datetime.datetime.now(IST).date()
    if _INSTR_CACHE is None or _CACHE_DATE != today:
        _INSTR_CACHE = kite_session().instruments("NFO")
        _CACHE_DATE  = today
    return _INSTR_CACHE

# ─── Quote helper ────────────────────────────────────────
def ltp_open_map(kite: KiteConnect, symbols: list[str]):
    """Return {symbol: (ltp, open)}; tolerate API errors."""
    out = {}
    for batch in (symbols[i:i+QUOTE_BATCH] for i in range(0, len(symbols), QUOTE_BATCH)):
        try:
            q = kite.quote(batch)
            for s, d in q.items():
                out[s] = (d["last_price"], d["ohlc"]["open"])
        except Exception:
            logging.warning("kite.quote failed for %s", batch)
    return out

# ─── Option-chain helpers ─────────────────────────────────
def next_expiry(scrip: str):
    today = datetime.datetime.now(IST).date()
    exps = sorted({i["expiry"] for i in instruments()
                   if i["name"] == scrip or i["tradingsymbol"].startswith(scrip)})
    for d in exps:
        if d >= today:
            return d
    return exps[-1]

def strikes_window(strikes: list[int], atm: int, width: int):
    if not strikes:
        return []
    idx = strikes.index(atm)
    return strikes[max(0, idx - width): idx + width + 1]

# ─── Premium-decay (robust) ──────────────────────────────
def compute_ce_pe_change(kite: KiteConnect, scrip: str):
    base = scrip.upper().replace("NSE:", "")
    # 1️⃣ spot
    spot = kite.ltp([f"NSE:{base}"])[f"NSE:{base}"]["last_price"]
    # 2️⃣ nearest expiry
    exp_dt = next_expiry(base)
    # 3️⃣ option rows for that expiry
    chain = [i for i in instruments()
             if i["name"] == base and i["expiry"] == exp_dt and
                i["instrument_type"] in {"CE", "PE"}]
    if not chain:
        return 0.0, 0.0

    # 4️⃣ pick ATM±1 strikes
    strikes = sorted({i["strike"] for i in chain})
    atm = min(strikes, key=lambda x: abs(x - spot))
    window = strikes_window(strikes, atm, WIDTH_DECAY)

    sel_rows = [i for i in chain if i["strike"] in window]
    prefixed = [f'NFO:{i["tradingsymbol"]}' for i in sel_rows]
    data_raw = ltp_open_map(kite, prefixed)
    if not data_raw:
        return 0.0, 0.0

    # 5️⃣ aggregate
    d_ce = d_pe = 0.0
    for row in sel_rows:
        key = f'NFO:{row["tradingsymbol"]}'
        if key in data_raw:
            ltp, opn = data_raw[key]
            diff = ltp - opn
            if row["instrument_type"] == "CE":
                d_ce += diff
            else:
                d_pe += diff
    return round(d_ce, 2), round(d_pe, 2)

# ─── Volume-spike rule (unchanged) ───────────────────────
def check_option(tsym: str, is_put: bool):
    token = next((i["instrument_token"] for i in instruments()
                  if i["tradingsymbol"] == tsym), None)
    if not token:
        return "❌"
    kite = kite_session()
    end   = datetime.datetime.now(IST)
    start = datetime.datetime.combine(end.date(), datetime.time(9, 15, tzinfo=IST))
    cds = kite.historical_data(token, start, end, "5minute")
    if not cds:
        return "❌"
    latest = cds[-1]
    if latest["volume"] != max(c["volume"] for c in cds):
        return "❌"
    green = latest["close"] > latest["open"]
    red   = latest["close"] < latest["open"]
    return "✅" if ((is_put and green) or (not is_put and red)) else "❌"

# ─── Alert persistence ───────────────────────────────────
def today_str(): return datetime.datetime.now(IST).strftime("%Y-%m-%d")

if not ALERTS_FILE.exists(): ALERTS_FILE.write_text("[]")
alerts = [a for a in json.loads(ALERTS_FILE.read_text()) if a.get("time","").startswith(today_str())]

def save_alert(row: dict):
    db = json.loads(ALERTS_FILE.read_text()) if ALERTS_FILE.exists() else []
    db.append(row)
    ALERTS_FILE.write_text(json.dumps(db, indent=2))
    alerts.append(row)

# ─── Flask routes ────────────────────────────────────────
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
def kite_callback():
    rt = request.args.get("request_token")
    if not rt:
        return "No request_token", 400
    kite = KiteConnect(api_key=KITE_API_KEY)
    data = kite.generate_session(rt, api_secret=KITE_API_SECRET)
    TOKEN_FILE.write_text(data["access_token"])
    return redirect(url_for("index"))

# ─── Webhook endpoint ───────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.get_json(force=True, silent=True) or {}
    symbol = payload.get("symbol")
    if not symbol:
        return "symbol missing", 400

    kite = kite_session()
    try:
        d_ce, d_pe = compute_ce_pe_change(kite, symbol)

        ltp = kite.ltp([f"NSE:{symbol.upper()}"])[f"NSE:{symbol.upper()}"]["last_price"]
        prev_close = kite.quote([f"NSE:{symbol.upper()}"])[f"NSE:{symbol.upper()}"]["ohlc"]["close"]
        move_pct = round((ltp - prev_close) / prev_close * 100, 2)

        exp_dt = next_expiry(symbol.upper())
        exp_code = exp_dt.strftime("%d%b%y").upper()
        strikes = strikes_window(
            sorted({i['strike'] for i in instruments() if i['name'] == symbol.upper() and i['expiry'] == exp_dt}),
            min(sorted({i['strike'] for i in instruments() if i['name'] == symbol.upper() and i['expiry'] == exp_dt}),
                key=lambda s: abs(s-ltp)),
            WIDTH_VOL
        )

        if strikes:
            puts, calls = [], []
            for st in strikes:
                pe_ts = f"{symbol.upper()}{exp_code}{st}PE"
                ce_ts = f"{symbol.upper()}{exp_code}{st}CE"
                puts.append(f"{st}{check_option(pe_ts, True)}")
                calls.append(f"{st}{check_option(ce_ts, False)}")
            put_result  = "  ".join(puts)
            call_result = "  ".join(calls)
        else:
            put_result = call_result = "No option chain"

        alert = {
            "symbol": symbol.upper(),
            "time": datetime.datetime.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
            "ltp": f"₹{ltp:.2f}",
            "move": move_pct,
            "ce_chg": d_ce,
            "pe_chg": d_pe,
            "put_result": put_result,
            "call_result": call_result,
        }
        save_alert(alert)

        if "✅" in put_result or "✅" in call_result:
            send_telegram(
                f"*New Signal* 📊\n"
                f"Symbol: `{alert['symbol']}`\n"
                f"Time  : {alert['time']}\n"
                f"LTP   : {alert['ltp']}\n"
                f"ΔCE   : {d_ce:+} | ΔPE: {d_pe:+}\n"
                f"PUT   : {put_result}\n"
                f"CALL  : {call_result}"
            )

        return "OK", 200
    except Exception:
        logging.exception("Webhook error")
        return "Error", 500

# ─── Dev runner ───────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 10000)))
