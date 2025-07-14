# app.py  –  option-chain + strike-tag display
# ─────────────────────────────────────────────────────────────────────────────
import os, json, datetime
from flask import Flask, request, render_template, redirect, url_for, session
from kiteconnect import KiteConnect

# ─── Time-zone helpers ───────────────────────────────────────────────────────
try:
    from zoneinfo import ZoneInfo          # Python ≥3.9
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:                        # fallback for older versions
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

UTC = datetime.timezone.utc

# ─── Config ──────────────────────────────────────────────────────────────────
WIDTH = 2            # strikes either side of ATM → ATM ± 2  (total 5 strikes)

# ─── Flask app ──────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "changeme")

# ─── Env / file paths ───────────────────────────────────────────────────────
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

# cache the instruments master once per day
INSTRUMENTS = None
INSTR_DATE  = None
def get_instruments():
    global INSTRUMENTS, INSTR_DATE
    today = datetime.datetime.now(IST).date()
    if INSTRUMENTS is None or INSTR_DATE != today:
        INSTRUMENTS = get_kite().instruments("NFO")
        INSTR_DATE  = today
    return INSTRUMENTS

# ─── Expiry helper ──────────────────────────────────────────────────────────
def expiry_date(symbol):
    today = datetime.datetime.now(IST).date()
    if symbol.upper() in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"}:
        # nearest Thursday
        days = 3 - today.weekday()
        if days < 0: days += 7
        exp = today + datetime.timedelta(days=days)
    else:
        # last Thursday of month
        tmp = today.replace(day=28) + datetime.timedelta(days=4)
        exp = tmp - datetime.timedelta(days=tmp.weekday() + 2)
    return exp.strftime("%Y-%m-%d")

# ─── Strike utilities ───────────────────────────────────────────────────────
def strikes_from_chain(symbol, expiry, spot, width=WIDTH):
    instruments = get_instruments()
    strikes_set = {
        inst["strike"]
        for inst in instruments
        if inst["name"] == symbol.upper()
        and inst["expiry"].strftime("%Y-%m-%d") == expiry
    }
    if not strikes_set:
        return []
    atm = min(strikes_set, key=lambda s: abs(s - spot))
    strikes_sorted = sorted(strikes_set)
    i = strikes_sorted.index(atm)
    start = max(0, i - width)
    end   = min(len(strikes_sorted), i + width + 1)
    return strikes_sorted[start:end]

def option_symbol(symbol, expiry, strike, opt_type):
    for inst in get_instruments():
        if (inst["name"] == symbol.upper()
            and inst["strike"] == strike
            and inst["expiry"].strftime("%Y-%m-%d") == expiry
            and inst["instrument_type"] == ("PE" if opt_type=="PUT" else "CE")):
            return inst["tradingsymbol"]
    return None

# ─── 5-min candle check ─────────────────────────────────────────────────────
def check_option(opt_symbol, is_put):
    kite = get_kite()
    try:
        end   = datetime.datetime.now(IST)
        start = datetime.datetime.combine(end.date(), datetime.time(9,15,tzinfo=IST))
        cds   = kite.historical_data(opt_symbol, start, end, "5minute")
        if not cds: return "❌"
        vols   = [c["volume"] for c in cds]
        latest = cds[-1]
        if latest["volume"] != max(vols): return "❌"
        green = latest["close"] > latest["open"]
        red   = latest["close"]  < latest["open"]
        return "✅" if ((is_put and green) or (not is_put and red)) else "❌"
    except Exception as e:
        print("check_option error:", e)
        return "❌"

# ─── Alert persistence ──────────────────────────────────────────────────────
def today_str(): return datetime.datetime.now(IST).strftime("%Y-%m-%d")

alerts = []
if os.path.exists(ALERTS_FILE):
    try:
        with open(ALERTS_FILE) as f:
            all_a = json.load(f)
            alerts = [a for a in all_a if a["time"].startswith(today_str())]
    except Exception as e:
        print("Load alerts:", e)

def save_alert(alert):
    try:
        all_a = []
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE) as f: all_a = json.load(f)
        all_a = [a for a in all_a if a["time"].startswith(today_str())]
        all_a.append(alert)
        with open(ALERTS_FILE,"w") as f: json.dump(all_a, f, indent=2)
        alerts.append(alert)
    except Exception as e:
        print("Save alert:", e)

# ─── Routes (login unchanged) ───────────────────────────────────────────────
@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login_page"))
    return render_template("index.html", alerts=alerts, kite_api_key=KITE_API_KEY)

@app.route("/login", methods=["GET","POST"])
def login_page():
    if request.method=="POST":
        if (request.form.get("username")==os.getenv("APP_USERNAME","admin") and
            request.form.get("password")==os.getenv("APP_PASSWORD","price123")):
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

# ─── Webhook core ───────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.json or {}
    symbol  = payload.get("symbol")
    if not symbol: return "Missing symbol", 400

    # trigger time (epoch-sec) from TradingView
    trg = payload.get("trigger_time")
    if trg:
        try: trig_dt = datetime.datetime.fromtimestamp(int(trg), UTC).astimezone(IST)
        except Exception: trig_dt = datetime.datetime.now(IST)
    else:
        trig_dt = datetime.datetime.now(IST)

    kite = get_kite()
    try:
        quote      = kite.ltp(f"NSE:{symbol.upper()}")[f"NSE:{symbol.upper()}"]
        spot_price = quote["last_price"]

        expiry  = expiry_date(symbol)
        strikes = strikes_from_chain(symbol, expiry, spot_price)
        if not strikes: return f"No strikes for {symbol}", 200

        put_tags, call_tags = [], []
        for st in strikes:
            pe = option_symbol(symbol, expiry, st, "PUT")
            ce = option_symbol(symbol, expiry, st, "CALL")
            p_res = check_option(f"NFO:{pe}",  True) if pe else "❌"
            c_res = check_option(f"NFO:{ce}", False) if ce else "❌"
            put_tags.append (f"{st}{p_res}")
            call_tags.append(f"{st}{c_res}")

        alert = {
            "symbol"     : symbol.upper(),
            "time"       : trig_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "ltp"        : f"₹{spot_price:.2f}",
            "put_result" : "  ".join(put_tags),     # strike+mark
            "call_result": "  ".join(call_tags),
        }
        save_alert(alert)
        print("Processed:", alert)
        return "OK", 200
    except Exception as e:
        print("Webhook error:", e)
        return "Error", 500

# ─── Local dev runner ───────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)

