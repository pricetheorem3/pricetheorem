# app.py  –  option-chain strikes, relaxed filter, fixed .date() bug
# ─────────────────────────────────────────────────────────────────────────────
import os, json, datetime
from flask import Flask, request, render_template, redirect, url_for, session
from kiteconnect import KiteConnect

# ─── Time-zone helpers ───────────────────────────────────────────────────────
try:
    from zoneinfo import ZoneInfo          # Python ≥3.9
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:                        # older runtimes
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

UTC = datetime.timezone.utc
WIDTH = 2                                  # ATM ± 2 strikes

# ─── Flask app ──────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "changeme")

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

# ─── Expiry helpers ─────────────────────────────────────────────────────────
def last_thu(y, m):
    d = datetime.date(y, m, 28) + datetime.timedelta(days=4)
    return d - datetime.timedelta(days=d.weekday() + 2)

def expiry_date(sym):
    today = datetime.datetime.now(IST).date()
    if sym.upper() in {"NIFTY","BANKNIFTY","FINNIFTY","MIDCPNIFTY"}:
        offs = 3 - today.weekday()
        if offs < 0: offs += 7
        return (today + datetime.timedelta(days=offs)).strftime("%Y-%m-%d")
    exp = last_thu(today.year, today.month)
    if today > exp:                         # if series expired, pick next
        nxt = (today + datetime.timedelta(days=32)).replace(day=1)
        exp = last_thu(nxt.year, nxt.month)
    return exp.strftime("%Y-%m-%d")

# ─── Strike utilities ───────────────────────────────────────────────────────
def _matches(sym, exp):
    s = sym.upper()
    return [
        i for i in get_instruments()
        if i["instrument_type"] in {"PE","CE"}
        and i["expiry"] == exp               # <-- fixed: no .date()
        and (i["name"] == s or i["tradingsymbol"].startswith(s))
    ]

def strikes_from_chain(sym, exp_str, spot, width=WIDTH):
    exp = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
    m   = _matches(sym, exp)

    if not m:                               # fallback → next-month expiry
        nxt = (exp + datetime.timedelta(days=32)).replace(day=1)
        exp = last_thu(nxt.year, nxt.month)
        m   = _matches(sym, exp)

    if not m: return []
    strikes = sorted({i["strike"] for i in m})
    atm     = min(strikes, key=lambda s: abs(s-spot))
    i       = strikes.index(atm)
    return strikes[max(0,i-width): i+width+1]

def option_symbol(sym, exp_str, strike, kind):
    exp = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
    for i in get_instruments():
        if (i["instrument_type"] == ("PE" if kind=="PUT" else "CE")
            and i["strike"] == strike
            and i["expiry"] == exp          # <-- fixed: no .date()
            and (i["name"] == sym.upper()
                 or i["tradingsymbol"].startswith(sym.upper()))):
            return i["tradingsymbol"]
    return None

# ─── 5-min candle check ────────────────────────────────────────────────────
def check_option(opt, is_put):
    kite = get_kite()
    try:
        end   = datetime.datetime.now(IST)
        start = datetime.datetime.combine(end.date(), datetime.time(9,15,tzinfo=IST))
        cds   = kite.historical_data(opt, start, end, "5minute")
        if not cds: return "❌"
        lat   = cds[-1]
        if lat["volume"] != max(c["volume"] for c in cds): return "❌"
        green = lat["close"] > lat["open"]
        red   = lat["close"]  < lat["open"]
        return "✅" if ((is_put and green) or (not is_put and red)) else "❌"
    except Exception as e:
        print("check_option error:", e)
        return "❌"

# ─── Alert persistence ─────────────────────────────────────────────────────
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
        all_a = []
        if os.path.exists(ALERTS_FILE):
            with open(ALERTS_FILE) as f: all_a = json.load(f)
        all_a = [x for x in all_a if x["time"].startswith(today())]
        all_a.append(a)
        with open(ALERTS_FILE,"w") as f: json.dump(all_a, f, indent=2)
        alerts.append(a)
    except Exception as e:
        print("Save alert:", e)

# ─── Routes (login unchanged) ──────────────────────────────────────────────
@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login_page"))
    return render_template("index.html", alerts=alerts, kite_api_key=KITE_API_KEY)

@app.route("/login", methods=["GET","POST"])
def login_page():
    if request.method=="POST":
        if (request.form.get("username")==os.getenv("APP_USERNAME","admin")
            and request.form.get("password")==os.getenv("APP_PASSWORD","price123")):
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

# ─── Webhook core ──────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    p       = request.json or {}
    symbol  = p.get("symbol")
    if not symbol: return "Missing symbol", 400

    trg = p.get("trigger_time")
    trig_dt = (datetime.datetime.fromtimestamp(int(trg), UTC).astimezone(IST)
               if trg else datetime.datetime.now(IST))

    kite = get_kite()
    try:
        spot = kite.ltp(f"NSE:{symbol.upper()}")[f"NSE:{symbol.upper()}"]["last_price"]
        expiry  = expiry_date(symbol)
        strikes = strikes_from_chain(symbol, expiry, spot)
        if not strikes:
            save_alert({
                "symbol": symbol.upper(), "time": trig_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "ltp": f"₹{spot:.2f}", "put_result": "No option chain",
                "call_result": "No option chain"
            })
            return "OK", 200

        put_tags, call_tags = [], []
        for st in strikes:
            pe = option_symbol(symbol, expiry, st, "PUT")
            ce = option_symbol(symbol, expiry, st, "CALL")
            put_tags.append (f"{st}{check_option(f'NFO:{pe}',  True) if pe else '❌'}")
            call_tags.append(f"{st}{check_option(f'NFO:{ce}', False) if ce else '❌'}")

        save_alert({
            "symbol": symbol.upper(),
            "time"  : trig_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "ltp"   : f"₹{spot:.2f}",
            "put_result" : "  ".join(put_tags),
            "call_result": "  ".join(call_tags)
        })
        return "OK", 200
    except Exception as e:
        print("Webhook error:", e)
        return "Error", 500

# ─── Local dev runner ──────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=True)
