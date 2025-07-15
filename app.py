# app.py – option‑chain screener (monthly) + Telegram alerts (env‑only)
# ─────────────────────────────────────────────────────────────────────
import os, json, datetime, requests
from flask import Flask, request, render_template, redirect, url_for, session
from kiteconnect import KiteConnect

# ─── Time‑zone helpers ───────────────────────────────────────────────
try:
    from zoneinfo import ZoneInfo          # Python ≥3.9
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

UTC, WIDTH = datetime.timezone.utc, 2      # ATM ±2 strikes

# ─── Flask app & base env ────────────────────────────────────────────
app = Flask(__name__)
app.secret_key   = os.getenv("FLASK_SECRET_KEY", "changeme")

KITE_API_KEY     = os.getenv("KITE_API_KEY")
KITE_API_SECRET  = os.getenv("KITE_API_SECRET")
TOKEN_FILE       = "access_token.txt"
ALERTS_FILE      = "alerts.json"

# ─── Telegram creds (must exist in env) ──────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError("Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID in environment")

# ─── Telegram helper ────────────────────────────────────────────────
def send_telegram(text: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}
    try:
        r = requests.post(url, data=payload, timeout=5)
        if r.status_code != 200:
            print("Telegram error:", r.text)
        return r.status_code == 200
    except Exception as e:
        print("Telegram exception:", e)
        return False

# ─── Kite helpers & instrument caching ──────────────────────────────
def get_kite():
    kite = KiteConnect(api_key=KITE_API_KEY)
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            kite.set_access_token(f.read().strip())
    return kite

INSTRUMENTS, INSTR_DATE = None, None
def get_instruments():
    global INSTRUMENTS, INSTR_DATE
    today = datetime.datetime.now(IST).date()
    if INSTRUMENTS is None or INSTR_DATE != today:
        INSTRUMENTS = get_kite().instruments("NFO")
        INSTR_DATE  = today
    return INSTRUMENTS

def token_for_symbol(tsym: str):
    for inst in get_instruments():
        if inst["tradingsymbol"] == tsym:
            return inst["instrument_token"]
    return None

# ─── Expiry helpers ─────────────────────────────────────────────────
def next_expiry(symbol: str):
    s, today = symbol.upper(), datetime.datetime.now(IST).date()
    dates = sorted({i["expiry"] for i in get_instruments()
                    if i["instrument_type"] in {"PE", "CE"} and
                       (i["name"] == s or i["tradingsymbol"].startswith(s))})
    for d in dates:
        if d >= today:
            return d
    return dates[-1]

def expiry_date(symbol: str):
    return next_expiry(symbol).strftime("%Y-%m-%d")

# ─── Option‑chain utilities ─────────────────────────────────────────
def _matches(sym, exp):
    s = sym.upper()
    return [i for i in get_instruments()
            if i["instrument_type"] in {"PE", "CE"} and
               i["expiry"] == exp and
               (i["name"] == s or i["tradingsymbol"].startswith(s))]

def strikes_from_chain(sym, exp_str, spot):
    exp = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
    m   = _matches(sym, exp)
    if not m: return []
    strikes = sorted({i["strike"] for i in m})
    atm     = min(strikes, key=lambda s: abs(s - spot))
    i       = strikes.index(atm)
    return strikes[max(0, i - WIDTH): i + WIDTH + 1]

def option_symbol(sym, exp_str, strike, kind):
    exp = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
    for i in get_instruments():
        if (i["instrument_type"] == ("PE" if kind == "PUT" else "CE") and
            i["strike"] == strike and i["expiry"] == exp and
            (i["name"] == sym.upper() or i["tradingsymbol"].startswith(sym.upper()))):
            return i["tradingsymbol"]
    return None

# ─── 5‑minute candle rule ───────────────────────────────────────────
def check_option(tsym, is_put):
    token = token_for_symbol(tsym)
    if token is None:
        print("No instrument_token for", tsym)
        return "❌"

    kite  = get_kite()
    end   = datetime.datetime.now(IST)
    start = datetime.datetime.combine(end.date(), datetime.time(9, 15, tzinfo=IST))
    try:
        cds = kite.historical_data(token, start, end, "5minute")
        if not cds:
            return "❌"
        latest = cds[-1]
        if latest["volume"] != max(c["volume"] for c in cds):
            return "❌"
        green = latest["close"] > latest["open"]
        red   = latest["close"]  < latest["open"]
        return "✅" if ((is_put and green) or (not is_put and red)) else "❌"
    except Exception as e:
        print("check_option error:", e)
        return "❌"

# ─── Alert persistence ──────────────────────────────────────────────
def today(): return datetime.datetime.now(IST).strftime("%Y-%m-%d")

alerts = []
if os.path.exists(ALERTS_FILE):
    try:
        with open(ALERTS_FILE) as f:
            alerts = [a for a in json.load(f) if a["time"].startswith(today())]
    except Exception as e:
        print("Load alerts:", e)

def save_alert(a):
    try:
        hist = []
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE) as f:
                hist = json.load(f)
        hist = [x for x in hist if x["time"].startswith(today())]
        hist.append(a)
        with open(ALERTS_FILE, "w") as f:
            json.dump(hist, f, indent=2)
        alerts.append(a)
    except Exception as e:
        print("Save alert:", e)

# ─── Routes (login etc.) ────────────────────────────────────────────
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
    if not rt: return "No request_token", 400
    try:
        kite = KiteConnect(api_key=KITE_API_KEY)
        data = kite.generate_session(rt, api_secret=KITE_API_SECRET)
        with open(TOKEN_FILE, "w") as f:
            f.write(data["access_token"])
        print("Access token saved.")
        return redirect(url_for("index"))
    except Exception as e:
        print("login_callback error:", e)
        return "Token generation failed", 500

# ─── Webhook core ───────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    p = request.json or {}
    symbol = p.get("symbol")
    if not symbol: return "Missing symbol", 400

    trg = p.get("trigger_time")
    if trg:
        try:
            trig_dt = datetime.datetime.fromtimestamp(int(trg), UTC).astimezone(IST)
        except (ValueError, TypeError):
            try:
                iso_dt = datetime.datetime.fromisoformat(trg.rstrip("Z"))
                if iso_dt.tzinfo is None: iso_dt = iso_dt.replace(tzinfo=UTC)
                trig_dt = iso_dt.astimezone(IST)
            except Exception:
                trig_dt = datetime.datetime.now(IST)
    else:
        trig_dt = datetime.datetime.now(IST)

    kite = get_kite()
    try:
        ltp = kite.ltp(f"NSE:{symbol.upper()}")[f"NSE:{symbol.upper()}"]["last_price"]
        expiry  = expiry_date(symbol)
        strikes = strikes_from_chain(symbol, expiry, ltp)

        if not strikes:
            put_result = call_result = "No option chain"
        else:
            put_tags, call_tags = [], []
            for st in strikes:
                pe = option_symbol(symbol, expiry, st, "PUT")
                ce = option_symbol(symbol, expiry, st, "CALL")
                put_tags.append (f"{st}{check_option(pe,  True) if pe else '❌'}")
                call_tags.append(f"{st}{check_option(ce, False) if ce else '❌'}")
            put_result  = "  ".join(put_tags)
            call_result = "  ".join(call_tags)

        alert = {
            "symbol": symbol.upper(),
            "time": trig_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "ltp": f"₹{ltp:.2f}",
            "put_result":  put_result,
            "call_result": call_result,
        }
        save_alert(alert)

        # Send Telegram only if at least one ✅ appears
        if "✅" in put_result or "✅" in call_result:
            msg = (
                f"*New Signal* 📊\n"
                f"Symbol: `{alert['symbol']}`\n"
                f"Time  : {alert['time']}\n"
                f"LTP   : {alert['ltp']}\n"
                f"PUT   : {put_result}\n"
                f"CALL  : {call_result}"
            )
            send_telegram(msg)

        return "OK", 200
    except Exception as e:
        print("Webhook error:", e)
        return "Error", 500

# ─── Local dev runner ───────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)

KITE_API_KEY    = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")
TOKEN_FILE      = "access_token.txt"
ALERTS_FILE     = "alerts.json"

# ─── Kite helpers ───────────────────────────────────────────────────────────
def get_kite():
    kite = KiteConnect(api_key=KITE_API_KEY)
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            kite.set_access_token(f.read().strip())
    return kite

INSTRUMENTS, INSTR_DATE = None, None
def get_instruments():
    global INSTRUMENTS, INSTR_DATE
    today = datetime.datetime.now(IST).date()
    if INSTRUMENTS is None or INSTR_DATE != today:
        INSTRUMENTS = get_kite().instruments("NFO")
        INSTR_DATE  = today
    return INSTRUMENTS

# ─── Token lookup helper ────────────────────────────────────────────────────
def token_for_symbol(tsym: str):
    for inst in get_instruments():
        if inst["tradingsymbol"] == tsym:
            return inst["instrument_token"]
    return None

# ─── Expiry helper (reads live chain) ───────────────────────────────────────
def next_expiry(symbol: str) -> datetime.date:
    s = symbol.upper()
    today = datetime.datetime.now(IST).date()
    dates = sorted({
        i["expiry"] for i in get_instruments()
        if i["instrument_type"] in {"PE", "CE"}
        and (i["name"] == s or i["tradingsymbol"].startswith(s))
    })
    for d in dates:
        if d >= today:
            return d
    return dates[-1]

def expiry_date(symbol: str) -> str:
    return next_expiry(symbol).strftime("%Y-%m-%d")

# ─── Option-chain utilities ────────────────────────────────────────────────
def _matches(sym, exp):
    s = sym.upper()
    return [
        i for i in get_instruments()
        if i["instrument_type"] in {"PE", "CE"}
        and i["expiry"] == exp
        and (i["name"] == s or i["tradingsymbol"].startswith(s))
    ]

def strikes_from_chain(sym, exp_str, spot):
    exp = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
    m   = _matches(sym, exp)
    if not m:
        return []
    strikes = sorted({i["strike"] for i in m})
    atm     = min(strikes, key=lambda s: abs(s - spot))
    i       = strikes.index(atm)
    return strikes[max(0, i-WIDTH): i+WIDTH+1]

def option_symbol(sym, exp_str, strike, kind):
    exp = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
    for i in get_instruments():
        if (i["instrument_type"] == ("PE" if kind=="PUT" else "CE")
            and i["strike"] == strike
            and i["expiry"] == exp
            and (i["name"] == sym.upper()
                 or i["tradingsymbol"].startswith(sym.upper()))):
            return i["tradingsymbol"]
    return None

# ─── 5-minute candle rule (now uses token) ─────────────────────────────────
def check_option(tsym, is_put):
    token = token_for_symbol(tsym)
    if token is None:
        print("No instrument_token for", tsym)
        return "❌"

    kite  = get_kite()
    end   = datetime.datetime.now(IST)
    start = datetime.datetime.combine(end.date(), datetime.time(9,15,tzinfo=IST))
    try:
        cds = kite.historical_data(token, start, end, "5minute")
        if not cds: return "❌"
        latest = cds[-1]
        if latest["volume"] != max(c["volume"] for c in cds): return "❌"
        green = latest["close"] > latest["open"]
        red   = latest["close"]  < latest["open"]
        return "✅" if ((is_put and green) or (not is_put and red)) else "❌"
    except Exception as e:
        print("check_option error:", e)
        return "❌"

# ─── Alert persistence ────────────────────────────────────────────────────
def today(): return datetime.datetime.now(IST).strftime("%Y-%m-%d")

alerts = []
if os.path.exists(ALERTS_FILE):
    try:
        with open(ALERTS_FILE) as f:
            alerts = [a for a in json.load(f) if a["time"].startswith(today())]
    except Exception as e:
        print("Load alerts:", e)

def save_alert(a):
    try:
        hist = []
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE) as f:
                hist = json.load(f)
        hist = [x for x in hist if x["time"].startswith(today())]
        hist.append(a)
        with open(ALERTS_FILE, "w") as f:
            json.dump(hist, f, indent=2)
        alerts.append(a)
    except Exception as e:
        print("Save alert:", e)

# ─── Routes (login unchanged) ─────────────────────────────────────────────
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
    if not rt: return "No request_token", 400
    try:
        kite = KiteConnect(api_key=KITE_API_KEY)
        data = kite.generate_session(rt, api_secret=KITE_API_SECRET)
        with open(TOKEN_FILE,"w") as f: f.write(data["access_token"])
        print("Access token saved.")
        return redirect(url_for("index"))
    except Exception as e:
        print("login_callback error:", e)
        return "Token generation failed", 500

# ─── Webhook core ─────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    p = request.json or {}
    symbol = p.get("symbol")
    if not symbol: return "Missing symbol", 400

    # robust trigger-time parser
    trg = p.get("trigger_time")
    if trg:
        try:
            trig_dt = datetime.datetime.fromtimestamp(int(trg), UTC).astimezone(IST)
        except (ValueError, TypeError):
            try:
                iso_dt = datetime.datetime.fromisoformat(trg.rstrip("Z"))
                if iso_dt.tzinfo is None: iso_dt = iso_dt.replace(tzinfo=UTC)
                trig_dt = iso_dt.astimezone(IST)
            except Exception:
                trig_dt = datetime.datetime.now(IST)
    else:
        trig_dt = datetime.datetime.now(IST)

    kite = get_kite()
    try:
        ltp = kite.ltp(f"NSE:{symbol.upper()}")[f"NSE:{symbol.upper()}"]["last_price"]
        expiry  = expiry_date(symbol)
        strikes = strikes_from_chain(symbol, expiry, ltp)

        if not strikes:
            save_alert({
                "symbol": symbol.upper(), "time": trig_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "ltp": f"₹{ltp:.2f}", "put_result": "No option chain",
                "call_result": "No option chain"
            })
            return "OK", 200

        put_tags, call_tags = [], []
        for st in strikes:
            pe = option_symbol(symbol, expiry, st, "PUT")
            ce = option_symbol(symbol, expiry, st, "CALL")
            put_tags.append (f"{st}{check_option(pe,  True) if pe else '❌'}")
            call_tags.append(f"{st}{check_option(ce, False) if ce else '❌'}")

        save_alert({
            "symbol": symbol.upper(), "time": trig_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "ltp": f"₹{ltp:.2f}", "put_result": "  ".join(put_tags),
            "call_result": "  ".join(call_tags)
        })
        return "OK", 200
    except Exception as e:
        print("Webhook error:", e)
        return "Error", 500

# ─── Local dev runner ───────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)

