# app.py
# ─────────────────────────────────────────────────────────────────────────────
import os, json, datetime
from flask import Flask, request, render_template, redirect, url_for, session
from kiteconnect import KiteConnect

# ─── Time-zone helpers ───────────────────────────────────────────────────────
try:                                    # Python ≥3.9
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:                     # fallback for older versions
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

UTC = datetime.timezone.utc

# ─── Flask app ──────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "changeme")

# ─── Env / file paths ───────────────────────────────────────────────────────
KITE_API_KEY    = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")
TOKEN_FILE      = "access_token.txt"
ALERTS_FILE     = "alerts.json"

# ─── Helpers ────────────────────────────────────────────────────────────────
def get_kite():
    kite = KiteConnect(api_key=KITE_API_KEY)
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            kite.set_access_token(f.read().strip())
    return kite

def today_str():
    """Return YYYY-MM-DD string in IST (not server’s UTC)."""
    return datetime.datetime.now(IST).strftime("%Y-%m-%d")

# Load only today’s alerts into memory
alerts = []
if os.path.exists(ALERTS_FILE):
    try:
        with open(ALERTS_FILE) as f:
            all_alerts = json.load(f)
            alerts = [a for a in all_alerts if a["time"].startswith(today_str())]
    except Exception as e:
        print("Failed to load alerts:", e)

def save_alert(alert):
    """Persist alert to file and keep in-memory list in sync (today only)."""
    try:
        all_alerts = []
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE) as f:
                all_alerts = json.load(f)
        all_alerts = [a for a in all_alerts if a["time"].startswith(today_str())]
        all_alerts.append(alert)
        with open(ALERTS_FILE, "w") as f:
            json.dump(all_alerts, f, indent=2)
        alerts.append(alert)
    except Exception as e:
        print("Error saving alert:", e)

# ─── Option helpers ────────────────────────────────────────────────────────
def expiry_date(symbol):
    """Indices → nearest Thursday; stocks → last Thursday of month."""
    today = datetime.datetime.now(IST).date()
    if symbol.upper() in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
        days = 3 - today.weekday()           # Monday=0
        if days < 0: days += 7
        exp = today + datetime.timedelta(days=days)
    else:
        next_month = today.replace(day=28) + datetime.timedelta(days=4)
        exp = next_month - datetime.timedelta(days=next_month.weekday() + 2)
    return exp.strftime("%Y-%m-%d")

def step(symbol):
    if "BANKNIFTY" in symbol.upper(): return 100
    if "NIFTY"     in symbol.upper(): return 50
    return 10

def strike_range(spot, step_size):
    atm = round(spot / step_size) * step_size
    return [atm + step_size * i for i in range(-2, 3)]    # ATM ±2 strikes

def find_option(symbol, expiry, strike, opt_type):
    kite = get_kite()
    for inst in kite.instruments("NFO"):
        if (inst["tradingsymbol"].startswith(symbol.upper())
            and inst["instrument_type"] == ("CE" if opt_type == "CALL" else "PE")
            and inst["strike"] == strike
            and inst["expiry"].strftime("%Y-%m-%d") == expiry):
            return inst["tradingsymbol"]
    return None

def check_option(opt_symbol, is_put):
    """
    ✅ = latest 5-min candle is highest-volume of the day AND
         colour rule: green for PUT, red for CALL
    ❌ otherwise (or no candles).
    """
    kite = get_kite()
    try:
        end   = datetime.datetime.now(IST)
        start = datetime.datetime.combine(end.date(), datetime.time(9, 15, tzinfo=IST))
        candles = kite.historical_data(opt_symbol, start, end, "5minute")
        if not candles:
            print(f"No 5-min candles for {opt_symbol}; skipping.")
            return "❌"
        volumes = [c["volume"] for c in candles]
        latest  = candles[-1]
        if latest["volume"] != max(volumes):
            return "❌"
        is_green = latest["close"] > latest["open"]
        is_red   = latest["close"] < latest["open"]
        return "✅" if ((is_put and is_green) or (not is_put and is_red)) else "❌"
    except Exception as e:
        print(f"check_option error for {opt_symbol}:", e)
        return "❌"

# ─── Routes ────────────────────────────────────────────────────────────────
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
        with open(TOKEN_FILE, "w") as f:
            f.write(data["access_token"])
        print("Access token generated and saved.")
        return redirect(url_for("index"))
    except Exception as e:
        print("login_callback error:", e)
        return "Token generation failed", 500

@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.json or {}
    symbol  = payload.get("symbol")
    if not symbol:
        return "Missing symbol", 400

    # ── Use TradingView’s trigger_time or fallback to now(IST) ───────────
    trig_epoch = payload.get("trigger_time")      # string of epoch-seconds
    if trig_epoch:
        try:
            trig_dt_utc = datetime.datetime.fromtimestamp(int(trig_epoch), UTC)
            trig_dt     = trig_dt_utc.astimezone(IST)
        except Exception:
            trig_dt = datetime.datetime.now(IST)
    else:
        trig_dt = datetime.datetime.now(IST)

    kite = get_kite()
    try:
        quote      = kite.ltp(f"NSE:{symbol.upper()}")[f"NSE:{symbol.upper()}"]
        spot_price = quote["last_price"]

        expiry  = expiry_date(symbol)
        step_sz = step(symbol)
        strikes = strike_range(spot_price, step_sz)

        put_res, call_res = [], []
        for st in strikes:
            pe = find_option(symbol, expiry, st, "PUT")
            ce = find_option(symbol, expiry, st, "CALL")
            put_res.append(check_option(f"NFO:{pe}",  True)  if pe else "❌")
            call_res.append(check_option(f"NFO:{ce}", False) if ce else "❌")

        alert = {
            "symbol"     : symbol.upper(),
            "time"       : trig_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "ltp"        : f"₹{spot_price:.2f}",
            "pct_move"   : "",             # optional: add %-move if you want
            "put_result" : " ".join(put_res),
            "call_result": " ".join(call_res),
        }
        save_alert(alert)
        print("Processed alert:", alert)
        return "OK", 200
    except Exception as e:
        print("Webhook error:", e)
        return "Error", 500

# ─── Local dev runner ────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)
