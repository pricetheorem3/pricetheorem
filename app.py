```python
# app.py — Optimized iGOT Screener
import os, json, datetime, logging
from flask import Flask, request, render_template, redirect, url_for, session
from kiteconnect import KiteConnect

# ─── Timezone Setup ─────────────────────────────────────────────────────
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    import pytz                   # fallback
    IST = pytz.timezone("Asia/Kolkata")
UTC = datetime.timezone.utc

# ─── Logging ──────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s: %(message)s"
)
log = logging.getLogger("iGOT")

# ─── Flask App ───────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "changeme")

# ─── Environment & File Paths ─────────────────────────────────────────────
KITE_API_KEY    = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")
TOKEN_FILE      = "access_token.txt"
ALERTS_FILE     = "alerts.json"

# ─── Kite Helper ─────────────────────────────────────────────────────────−
def get_kite():
    kite = KiteConnect(api_key=KITE_API_KEY)
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            kite.set_access_token(f.read().strip())
    return kite

# ─── Date Helpers ────────────────────────────────────────────────────────
def today_str():
    return datetime.datetime.now(IST).strftime("%Y-%m-%d")

# ─── In-Memory Alerts ────────────────────────────────────────────────────
alerts = []
if os.path.exists(ALERTS_FILE):
    try:
        with open(ALERTS_FILE) as f:
            stored = json.load(f)
        alerts = [a for a in stored if a.get("time", "").startswith(today_str())]
    except Exception as e:
        log.warning("Failed to load alerts: %s", e)

# ─── Persistence ────────────────────────────────────────────────────────
def save_alert(alert):
    try:
        all_alerts = []
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE) as f:
                all_alerts = json.load(f)
        # keep only today's
        all_alerts = [a for a in all_alerts if a.get("time", "").startswith(today_str())]
        all_alerts.append(alert)
        with open(ALERTS_FILE, "w") as f:
            json.dump(all_alerts, f, indent=2)
        alerts.append(alert)
    except Exception as e:
        log.error("Error saving alert: %s", e)

# ─── Option Utilities ───────────────────────────────────────────────────
def expiry_date(symbol):
    today = datetime.datetime.now(IST).date()
    if symbol.upper() in ["NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"]:
        days_to_thu = (3 - today.weekday()) % 7
        exp = today + datetime.timedelta(days=days_to_thu)
    else:
        nxt = today.replace(day=28) + datetime.timedelta(days=4)
        exp = nxt - datetime.timedelta(days=(nxt.weekday()+2)%7)
    return exp.strftime("%Y-%m-%d")

def step(symbol):
    u = symbol.upper()
    if "BANKNIFTY" in u: return 100
    if "NIFTY" in u:     return 50
    return 10

def strike_range(spot, step_size):
    atm = round(spot/step_size) * step_size
    return [atm + step_size*i for i in range(-1,2)]  # ATM ±1

def find_option(symbol, expiry, strike, opt_type):
    kite = get_kite()
    for inst in kite.instruments("NFO"):
        if (inst["tradingsymbol"].startswith(symbol.upper()) and
            inst["instrument_type"] == ("CE" if opt_type=="CALL" else "PE") and
            inst["strike"] == strike and
            inst["expiry"].strftime("%Y-%m-%d") == expiry):
            return inst["tradingsymbol"]
    return None

def check_option(opt_symbol, is_put):
    try:
        kite = get_kite()
        end = datetime.datetime.now(IST)
        start = datetime.datetime.combine(end.date(), datetime.time(9,15, tzinfo=IST))
        candles = kite.historical_data(opt_symbol, start, end, "5minute")
        if not candles:
            log.info("No candles for %s", opt_symbol)
            return "❌"
        max_vol = max(c["volume"] for c in candles)
        last = candles[-1]
        if last["volume"] < max_vol:
            return "❌"
        green = last["close"] > last["open"]
        red   = last["close"] < last["open"]
        return "✅" if ((is_put and green) or (not is_put and red)) else "❌"
    except Exception as e:
        log.error("check_option error for %s: %s", opt_symbol, e)
        return "❌"

# ─── Routes ─────────────────────────────────────────────────────────────
@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login_page"))
    return render_template("index.html", alerts=alerts, kite_api_key=KITE_API_KEY)

@app.route("/login", methods=["GET","POST"])
def login_page():
    error = None
    if request.method == "POST":
        if (request.form.get("username") == os.getenv("APP_USERNAME","admin") and
            request.form.get("password") == os.getenv("APP_PASSWORD","price123")):
            session["logged_in"] = True
            return redirect(url_for("index"))
        error = "Invalid credentials"
    return render_template("login.html", error=error)

@app.route("/login/callback")
def login_callback():
    rt = request.args.get("request_token")
    if not rt:
        return "No request_token", 400
    try:
        kite = KiteConnect(api_key=KITE_API_KEY)
        data = kite.generate_session(rt, api_secret=KITE_API_SECRET)
        with open(TOKEN_FILE, "w") as f:
            f.write(data["access_token"])
        log.info("Access token generated and saved.")
        return redirect(url_for("index"))
    except Exception as e:
        log.error("login_callback error: %s", e)
        return "Token generation failed", 500

@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.json or {}
    symbol = payload.get("symbol")
    if not symbol:
        return "Missing symbol", 400

    trig = payload.get("trigger_time")
    try:
        if trig:
            dt = datetime.datetime.fromtimestamp(int(trig), UTC).astimezone(IST)
        else:
            dt = datetime.datetime.now(IST)
    except:
        dt = datetime.datetime.now(IST)

    try:
        kite  = get_kite()
        quote = kite.ltp(f"NSE:{symbol.upper()}")[f"NSE:{symbol.upper()}"]
        spot  = quote["last_price"]
        exp   = expiry_date(symbol)
        sz    = step(symbol)
        sts   = strike_range(spot, sz)

        put_res = []
        call_res= []
        for st in sts:
            pe = find_option(symbol, exp, st, "PUT")
            ce = find_option(symbol, exp, st, "CALL")
            put_res.append(check_option(f"NFO:{pe}", True)  if pe else "❌")
            call_res.append(check_option(f"NFO:{ce}", False) if ce else "❌")

        alert = {
            "symbol": symbol.upper(),
            "time"  : dt.strftime("%Y-%m-%d %H:%M:%S"),
            "ltp"   : f"₹{spot:.2f}",
            "put_result" : " ".join(put_res),
            "call_result": " ".join(call_res)
        }
        save_alert(alert)
        log.info("Processed alert: %s", alert)
        return "OK", 200

    except Exception as e:
        log.error("Webhook error: %s", e)
        return "Error", 500

# ─── Runner ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)), debug=True)
```
