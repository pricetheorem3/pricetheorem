# app.py – Final Version for Render + Gunicorn
import os, json, datetime, requests
from flask import Flask, request, render_template, redirect, url_for, session
from kiteconnect import KiteConnect

# Time-zone setup
try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

UTC = datetime.timezone.utc

# Global app instance for Gunicorn
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "supersecret")

# Load API key/secret from env
KITE_API_KEY = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")
KITE_ACCESS_TOKEN = None
TOKEN_FILE = os.path.join(".", "token.json")
ALERTS_FILE = os.path.join(".", "alerts.json")
IV_FILE = os.path.join(".", "iv_baseline.json")

# Load/save token helpers
def save_token(token):
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f)

def load_token():
    if os.path.exists(TOKEN_FILE):
        return json.load(open(TOKEN_FILE))
    return None

def _token_available():
    return os.path.exists(TOKEN_FILE)

# Get KiteConnect instance
def get_kite():
    token = load_token()
    kite = KiteConnect(api_key=KITE_API_KEY)
    if token:
        kite.set_access_token(token["access_token"])
    return kite

# Telegram alert (optional)
def send_telegram(msg):
    bot = os.getenv("TG_BOT_TOKEN")
    cid = os.getenv("TG_CHAT_ID")
    if not bot or not cid: return
    requests.get(f"https://api.telegram.org/bot{bot}/sendMessage",
                 params={"chat_id": cid, "text": msg, "parse_mode": "Markdown"})

# Save alerts
def save_alert(alert):
    if os.path.exists(ALERTS_FILE):
        alerts = json.load(open(ALERTS_FILE))
    else:
        alerts = []
    alerts.insert(0, alert)
    with open(ALERTS_FILE, "w") as f:
        json.dump(alerts[:100], f, indent=2)

# IV estimation stub (real formula should be added later)
def implied_vol(price, spot, strike, time, cp=1):
    return 0.25  # Placeholder

# === Routes ===

@app.route("/")
def index():
    if not _token_available():
        return render_template("index.html", alerts=[], token=False)
    alerts = json.load(open(ALERTS_FILE)) if os.path.exists(ALERTS_FILE) else []
    return render_template("index.html", alerts=alerts, token=True)

@app.route("/login")
def login():
    kite = KiteConnect(api_key=KITE_API_KEY)
    return redirect(kite.login_url())

@app.route("/logout")
def logout():
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)
    return redirect("/")

@app.route("/login/callback")
def login_callback():
    kite = KiteConnect(api_key=KITE_API_KEY)
    request_token = request.args.get("request_token")
    data = kite.generate_session(request_token, api_secret=KITE_API_SECRET)
    save_token(data)
    return redirect("/")

@app.route("/webhook", methods=["POST"])
def webhook():
    if not _token_available(): return "Kite not connected", 503
    data = request.get_json(force=True)
    if not data or "symbol" not in data: return "Bad payload", 400

    sym = data["symbol"].upper()
    move = data.get("move", "")
    now = datetime.datetime.now(IST)
    ts = now.strftime("%Y-%m-%d %H:%M:%S")

    kite = get_kite()
    spot = kite.ltp(f"NSE:{sym}")[f"NSE:{sym}"]["last_price"]

    alert = {"symbol": sym, "time": ts, "move": move, "ltp": spot}
    alert |= {k: None for k in (
        "ΔCE", "ΔPE", "Skew", "ΔOI_PUT", "call_vol",
        "trend", "flag", "IVΔ_CE", "IVΔ_PE",
        "iv_flag", "signal", "call_result", "put_result")}

    try:
        inst = [i for i in kite.instruments("NFO") if i["name"] == sym]
        if not inst:
            raise ValueError("Not in F&O")

        exp_date = sorted({i["expiry"] for i in inst})[0]
        strikes = sorted({i["strike"] for i in inst if i["expiry"] == exp_date})
        atm = min(strikes, key=lambda k: abs(k - spot))

        inst_ce = next(i for i in inst if i["strike"] == atm and i["instrument_type"] == "CE")
        inst_pe = next(i for i in inst if i["strike"] == atm and i["instrument_type"] == "PE")
        ce_atm = inst_ce["tradingsymbol"]
        pe_atm = inst_pe["tradingsymbol"]

        dce = dpe = 0
        for k in [x for x in strikes if x >= atm][:3]:
            ts = next(i["tradingsymbol"] for i in inst if i["strike"] == k and i["instrument_type"] == "CE")
            q = kite.quote(ts)[ts]
            dce += q["last_price"] - q["ohlc"]["open"]
        for k in [x for x in strikes if x <= atm][-3:]:
            ts = next(i["tradingsymbol"] for i in inst if i["strike"] == k and i["instrument_type"] == "PE")
            q = kite.quote(ts)[ts]
            dpe += q["last_price"] - q["ohlc"]["open"]

        alert["ΔCE"], alert["ΔPE"] = round(dce, 2), round(dpe, 2)

        def iv_now(ts, cp):
            q = kite.quote(ts)[ts]
            T = (exp_date.date() - now.date()).days / 365
            return implied_vol(q["last_price"], spot, atm, T, cp=cp)

        iv_ce, iv_pe = iv_now(ce_atm, 1), iv_now(pe_atm, -1)
        alert["Skew"] = round(iv_ce - iv_pe, 4)

        alert["call_vol"] = round(kite.quote(ce_atm)[ce_atm]["volume"] / 1000, 2)
        alert["ΔOI_PUT"] = kite.quote(pe_atm)[pe_atm]["oi"]

        alert["call_result"] = (
            "✅" if kite.quote(ce_atm)[ce_atm]["last_price"] >
                     kite.quote(ce_atm)[ce_atm]["ohlc"]["open"] else "❌")
        alert["put_result"] = (
            "✅" if kite.quote(pe_atm)[pe_atm]["last_price"] >
                     kite.quote(pe_atm)[pe_atm]["ohlc"]["open"] else "❌")

        alert["trend"] = "Bullish" if dce > abs(dpe) else "Bearish" if dpe > abs(dce) else "Flat"
        alert["flag"] = "Flat PE" if alert["ΔPE"] < 0 else (
                        "Strong CE" if alert["ΔCE"] > 3 else "")

        baseline = json.load(open(IV_FILE)) if os.path.exists(IV_FILE) else {}
        iv0_ce = baseline.get(f"{sym}_CE", iv_ce)
        iv0_pe = baseline.get(f"{sym}_PE", iv_pe)
        alert["IVΔ_CE"], alert["IVΔ_PE"] = round(iv_ce - iv0_ce, 4), round(iv_pe - iv0_pe, 4)

        thr = 0.03
        alert["iv_flag"] = (
            "IV Pump" if max(alert["IVΔ_CE"], alert["IVΔ_PE"]) >= thr else
            "IV Crush" if min(alert["IVΔ_CE"], alert["IVΔ_PE"]) <= -thr else "")
        alert["signal"] = f"{alert['trend']} {alert['iv_flag']}".strip()

    except Exception as e:
        alert["error"] = str(e)

    save_alert(alert)
    send_telegram(f"*Alert • {sym}* `{ts}`\nMove: {move}")
    return "OK"

# No need for app.run() – Gunicorn will use `app`

