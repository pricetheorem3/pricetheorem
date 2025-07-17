# app.py – Final version with CE/PE premium‑change, volume spike logic, and Telegram alert for all signals

import os, json, datetime, logging, pathlib, requests, itertools
from flask import Flask, request, render_template, redirect, url_for, session
from kiteconnect import KiteConnect

logging.basicConfig(level=os.getenv("LOG_LEVEL", "WARNING"))

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    import pytz; IST = pytz.timezone("Asia/Kolkata")

UTC = datetime.timezone.utc
WIDTH_VOL = 2
WIDTH_DECAY = 1
STRIKE_STEP = 10
QUOTE_BATCH = 25

DATA_DIR = pathlib.Path(os.getenv("DATA_DIR", "."))
ALERTS_FILE = DATA_DIR / "alerts.json"
TOKEN_FILE = DATA_DIR / "access_token.txt"

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "changeme")
KITE_API_KEY = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
    raise RuntimeError("Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID")

def send_telegram(msg: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception:
        logging.warning("Telegram send failed")

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
        _CACHE_DATE = today
    return _INSTR_CACHE

def price_step(price: float) -> int:
    return 5 if price < 500 else STRIKE_STEP

def opt_symbol(base: str, exp_code: str, strike: int, kind: str) -> str:
    return f"{base}{exp_code}{strike}{kind}"

def chunked(iterable, n):
    it = iter(iterable)
    while (batch := list(itertools.islice(it, n))):
        yield batch

def ltp_open_map(kite: KiteConnect, symbols: list[str]):
    out = {}
    if not symbols:
        return out
    for batch in chunked(symbols, QUOTE_BATCH):
        try:
            q = kite.quote(batch)
            for s, d in q.items():
                out[s] = (d["last_price"], d["ohlc"]["open"])
        except Exception:
            logging.warning("kite.quote failed for %s", batch)
    return out

def next_expiry(scrip: str):
    today = datetime.date.today()
    exps = sorted({i["expiry"] for i in instruments()
                   if i["instrument_type"] in {"PE", "CE"} and
                   (i["name"] == scrip or i["tradingsymbol"].startswith(scrip))})
    for d in exps:
        if d >= today:
            return d
    return exps[-1]

def strikes_from_chain(scrip: str, exp_dt: datetime.date, spot: float):
    chain = [i for i in instruments()
             if i["expiry"] == exp_dt and
             (i["name"] == scrip or i["tradingsymbol"].startswith(scrip))]
    strikes = sorted({i["strike"] for i in chain})
    if not strikes:
        return []
    atm = min(strikes, key=lambda x: abs(x - spot))
    idx = strikes.index(atm)
    return strikes[max(0, idx - WIDTH_VOL): idx + WIDTH_VOL + 1]

def nfo_exists(tsym: str) -> bool:
    return any(i["tradingsymbol"] == tsym for i in instruments())

def compute_ce_pe_change(kite: KiteConnect, scrip: str):
    base = scrip.upper().replace("NSE:", "")
    spot = kite.ltp([f"NSE:{base}"])[f"NSE:{base}"]["last_price"]
    exp_dt = next_expiry(base)
    exp_code = exp_dt.strftime("%d%b%y").upper()
    strikes = strikes_from_chain(base, exp_dt, spot)
    if not strikes:
        return 0.0, 0.0
    prefixed = [f"NFO:{opt_symbol(base, exp_code, st, kind)}"
                for st in strikes[: WIDTH_DECAY * 2 + 1]
                for kind in ("CE", "PE")
                if nfo_exists(opt_symbol(base, exp_code, st, kind))]
    data_raw = ltp_open_map(kite, prefixed)
    if not data_raw:
        return 0.0, 0.0
    data = {k.split(":")[1]: v for k, v in data_raw.items()}
    d_ce = d_pe = 0.0
    for st in strikes:
        ce = opt_symbol(base, exp_code, st, "CE")
        pe = opt_symbol(base, exp_code, st, "PE")
        if ce in data:
            ltp, opn = data[ce]; d_ce += ltp - opn
        if pe in data:
            ltp, opn = data[pe]; d_pe += ltp - opn
    return round(d_ce, 2), round(d_pe, 2)

def check_option(tsym: str, is_put: bool):
    token = next((i["instrument_token"] for i in instruments()
                  if i["tradingsymbol"] == tsym), None)
    if not token:
        return "❌"
    kite = kite_session()
    end = datetime.datetime.now(IST)
    start = datetime.datetime.combine(end.date(), datetime.time(9, 15, tzinfo=IST))
    cds = kite.historical_data(token, start, end, "5minute")
    if not cds:
        return "❌"
    latest = cds[-1]
    if latest["volume"] != max(c["volume"] for c in cds):
        return "❌"
    green = latest["close"] > latest["open"]
    red = latest["close"] < latest["open"]
    return "✅" if ((is_put and green) or (not is_put and red)) else "❌"

def today_str():
    return datetime.datetime.now(IST).strftime("%Y-%m-%d")

if not ALERTS_FILE.exists():
    ALERTS_FILE.write_text("[]")

try:
    alerts = [a for a in json.loads(ALERTS_FILE.read_text())
              if a.get("time", "").startswith(today_str())]
except Exception:
    logging.exception("Load alerts file"); alerts = []

def save_alert(row: dict):
    try:
        db = json.loads(ALERTS_FILE.read_text())
    except Exception:
        db = []
    db.append(row)
    ALERTS_FILE.write_text(json.dumps(db, indent=2))
    alerts.append(row)

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

        exp_dt = next_expiry(symbol)
        strikes = strikes_from_chain(symbol.upper(), exp_dt, ltp)
        exp_code = exp_dt.strftime("%d%b").upper()
        if strikes:
            puts, calls = [], []
            for st in strikes:
                pe_ts = opt_symbol(symbol.upper(), exp_code, st, "PE")
                ce_ts = opt_symbol(symbol.upper(), exp_code, st, "CE")
                puts.append(f"{st}{check_option(pe_ts, True)}")
                calls.append(f"{st}{check_option(ce_ts, False)}")
            put_result = "  ".join(puts)
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

if __name__ == "__main__":
    app.run(debug=True, port=int(os.getenv("PORT", 10000)))
