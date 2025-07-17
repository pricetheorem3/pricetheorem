# app.py — iGOT screener (SQLite + IV‑pump + Signal column + Kite OAuth)
# ────────────────────────────────────────────────────────────────────
# Required ENV variables on Render / .env:
# KITE_API_KEY        = ...
# KITE_API_SECRET     = ...
# FLASK_SECRET_KEY    = any‑random‑string
# APP_USERNAME        = admin      # optional
# APP_PASSWORD        = price123   # optional
# TELEGRAM_TOKEN      = ...        # optional
# TELEGRAM_CHAT_ID    = ...        # optional
# WATCHLIST           = NSE:SBIN,NSE:RELIANCE,... (optional)
# DATA_DIR            = /data      # if you attached a Render disk (optional)
# PORT                = 10000      # Render detects this automatically

import os, json, math, time, datetime, threading, logging, sqlite3, requests
from zoneinfo import ZoneInfo

from flask import Flask, render_template, request, redirect, url_for, session
from kiteconnect import KiteConnect
from db import init_db, DB_FILE   # your existing tiny helper module

# ─── Config & globals ───────────────────────────────────────────────
IST             = ZoneInfo("Asia/Kolkata")
DATA_DIR        = os.getenv("DATA_DIR", ".")
TOKEN_PATH      = os.path.join(DATA_DIR, "access_token.txt")
IV_FILE         = "iv_915.json"

KITE_API_KEY    = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID= os.getenv("TELEGRAM_CHAT_ID")

APP_USER        = os.getenv("APP_USERNAME", "admin")
APP_PASS        = os.getenv("APP_PASSWORD", "price123")
FLASK_SECRET    = os.getenv("FLASK_SECRET_KEY", "changeme")
WATCHLIST       = [s.strip().split(":")[-1]
                   for s in os.getenv("WATCHLIST", "").split(",") if s.strip()]

app = Flask(__name__)
app.secret_key = FLASK_SECRET

init_db()
logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
log = logging.getLogger("iGOT")

# ─── Token helpers ─────────────────────────────────────────────────
def _token_available() -> bool:
    return os.path.exists(TOKEN_PATH) and os.path.getsize(TOKEN_PATH) > 0

def _read_token() -> str:
    return open(TOKEN_PATH).read().strip() if _token_available() else ""

def _write_token(tok: str):
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        f.write(tok)

def get_kite() -> KiteConnect:
    kite = KiteConnect(api_key=KITE_API_KEY)
    if _token_available():
        kite.set_access_token(_read_token())
    return kite

# ─── Utility I/O ───────────────────────────────────────────────────
def send_telegram(msg: str):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID):
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception as e:
        log.warning("Telegram error: %s", e)

def save_alert(a: dict):
    """Insert alert row into SQLite."""
    with sqlite3.connect(DB_FILE) as c:
        c.execute(
            """
        INSERT INTO alerts (
            symbol,time,move,ltp,dce,dpe,skew,doi_put,call_vol,
            trend,flag,ivd_ce,ivd_pe,iv_flag,signal,
            call_result,put_result)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
            (
                a["symbol"], a["time"], a["move"], a["ltp"], a["ΔCE"], a["ΔPE"],
                a["Skew"], a["ΔOI_PUT"], a["call_vol"],
                a["trend"], a["flag"], a["IVΔ_CE"], a["IVΔ_PE"], a["iv_flag"],
                a["signal"], a["call_result"], a["put_result"],
            ),
        )

def load_alerts_for(day: str):
    with sqlite3.connect(DB_FILE) as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM alerts WHERE time LIKE ? ORDER BY time DESC",
                    (f"{day}%",))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

# ─── Black‑Scholes helper (implied volatility) ─────────────────────
def _bs_price(S, K, T, r, σ, q, cp):
    if σ <= 0 or T <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * σ * σ) * T) / (σ * math.sqrt(T))
    d2 = d1 - σ * math.sqrt(T)
    Φ = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return cp * (S * math.exp(-q * T) * Φ(cp * d1) - K * math.exp(-r * T) * Φ(cp * d2))

def implied_vol(price, S, K, T, r=0.07, q=0.0, cp=1):
    lo, hi = 1e-6, 5
    for _ in range(60):
        mid = (lo + hi) / 2
        if _bs_price(S, K, T, r, mid, q, cp) > price:
            hi = mid
        else:
            lo = mid
    return round((lo + hi) / 2, 4)

# ─── 9:15 IV baseline job ──────────────────────────────────────────
def capture_iv_snapshot():
    if not WATCHLIST or not _token_available():
        return
    kite = get_kite()
    now = datetime.datetime.now(IST)
    ivs = {}
    for sym in WATCHLIST:
        try:
            inst = [i for i in kite.instruments("NFO") if i["name"] == sym]
            exp = sorted({i["expiry"] for i in inst})[0].strftime("%Y-%m-%d")
            spot = kite.ltp(f"NSE:{sym}")[f"NSE:{sym}"]["last_price"]
            strikes = sorted({i["strike"] for i in inst
                              if i["expiry"].strftime("%Y-%m-%d") == exp})
            atm = min(strikes, key=lambda k: abs(k - spot))
            fmt = lambda k, t: f"{sym}{exp[2:4]}{exp[5:7]}{int(k):05d}{t}"
            for t, cp in (("CE", 1), ("PE", -1)):
                ts = fmt(atm, t)
                q = kite.quote(ts)[ts]
                K = float(ts[-7:-2])
                T = (datetime.datetime.strptime(exp, "%Y-%m-%d").date() -
                     now.date()).days / 365
                ivs[f"{sym}_{t}"] = implied_vol(q["last_price"], spot, K, T, cp=cp)
        except Exception as e:
            log.debug("IV snapshot error for %s • %s", sym, e)
    json.dump(ivs, open(IV_FILE, "w"))
    log.info("IV baseline captured: %d symbols", len(ivs))

def schedule_iv_job():
    def worker():
        while True:
            now = datetime.datetime.now(IST)
            tgt = now.replace(hour=9, minute=15, second=0, microsecond=0)
            if now >= tgt:
                tgt += datetime.timedelta(days=1)
            time.sleep((tgt - now).total_seconds())
            capture_iv_snapshot()
    threading.Thread(target=worker, daemon=True).start()

schedule_iv_job()

# ─── Kite OAuth flow ────────────────────────────────────────────────
@app.route("/kite/auth")
def kite_auth():
    kite = KiteConnect(api_key=KITE_API_KEY)
    return redirect(kite.login_url())

@app.route("/kite/callback")
def kite_callback():
    if request.args.get("status") != "success":
        return "Kite login failed", 400
    req_token = request.args.get("request_token")
    if not req_token:
        return "Missing request_token", 400
    kite = KiteConnect(api_key=KITE_API_KEY)
    try:
        data = kite.generate_session(req_token, api_secret=KITE_API_SECRET)
    except Exception as e:
        log.error("generate_session failed: %s", e)
        return f"Kite session error: {e}", 400
    _write_token(data["access_token"])
    log.info("Kite access_token stored")
    session["kite_connected"] = True
    return redirect(url_for("index"))

# ─── Webhook from TradingView ──────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    if not _token_available():
        return "Kite not connected", 503
    data = request.get_json(force=True)
    if not data or "symbol" not in data:
        return "Bad payload", 400

    sym  = data["symbol"].upper()
    move = data.get("move", "")
    now  = datetime.datetime.now(IST)
    ts   = now.strftime("%Y-%m-%d %H:%M:%S")

    kite = get_kite()
    spot = kite.ltp(f"NSE:{sym}")[f"NSE:{sym}"]["last_price"]
    alert = {"symbol": sym, "time": ts, "move": move, "ltp": spot}

    try:
        inst = [i for i in kite.instruments("NFO") if i["name"] == sym]
        exp  = sorted({i["expiry"] for i in inst})[0].strftime("%Y-%m-%d")
        strikes = sorted({i["strike"] for i in inst
                          if i["expiry"].strftime("%Y-%m-%d") == exp})
        atm = min(strikes, key=lambda k: abs(k - spot))
        fmt = lambda k, t: f"{sym}{exp[2:4]}{exp[5:7]}{int(k):05d}{t}"

        # ΔCE / ΔPE
        dce = dpe = 0
        for k in [x for x in strikes if x >= atm][:3]:
            q = kite.quote(fmt(k, "CE"))[fmt(k, "CE")]
            dce += q["last_price"] - q["ohlc"]["open"]
        for k in [x for x in strikes if x <= atm][-3:]:
            q = kite.quote(fmt(k, "PE"))[fmt(k, "PE")]
            dpe += q["last_price"] - q["ohlc"]["open"]
        alert["ΔCE"], alert["ΔPE"] = round(dce, 2), round(dpe, 2)

        ce_atm, pe_atm = fmt(atm, "CE"), fmt(atm, "PE")

        def iv_now(ts, cp):
            q = kite.quote(ts)[ts]
            K = float(ts[-7:-2])
            T = (datetime.datetime.strptime(exp, "%Y-%m-%d").date() -
                 now.date()).days / 365
            return implied_vol(q["last_price"], spot, K, T, cp=cp)
        iv_ce, iv_pe = iv_now(ce_atm, 1), iv_now(pe_atm, -1)
        alert["Skew"] = round(iv_ce - iv_pe, 4)

        alert["call_vol"]  = round(kite.quote(ce_atm)[ce_atm]["volume"] / 1000, 2)
        alert["ΔOI_PUT"]   = kite.quote(pe_atm)[pe_atm]["oi"]

        alert["call_result"] = (
            "✅" if kite.quote(ce_atm)[ce_atm]["last_price"] >
                   kite.quote(ce_atm)[ce_atm]["ohlc"]["open"] else "❌")
        alert["put_result"] = (
            "✅" if kite.quote(pe_atm)[pe_atm]["last_price"] >
                   kite.quote(pe_atm)[pe_atm]["ohlc"]["open"] else "❌")

        alert["trend"] = ("Bullish" if dce > abs(dpe)
                          else "Bearish" if dpe > abs(dce) else "Flat")
        alert["flag"] = "Flat PE" if alert["ΔPE"] < 0 else (
                        "Strong CE" if alert["ΔCE"] > 3 else "")

        baseline = json.load(open(IV_FILE)) if os.path.exists(IV_FILE) else {}
        iv0_ce   = baseline.get(f"{sym}_CE", iv_ce)
        iv0_pe   = baseline.get(f"{sym}_PE", iv_pe)
        alert["IVΔ_CE"], alert["IVΔ_PE"] = round(iv_ce - iv0_ce, 4), round(iv_pe - iv0_pe, 4)

        thr = 0.03
        alert["iv_flag"] = ("IV Pump"  if max(alert["IVΔ_CE"], alert["IVΔ_PE"]) >= thr else
                            "IV Crush" if min(alert["IVΔ_CE"], alert["IVΔ_PE"]) <= -thr else "")
        alert["signal"] = f"{alert['trend']} {alert['iv_flag']}".strip()

    except Exception as e:
        log.warning("Webhook calc error: %s", e)
        alert["error"] = str(e)

    save_alert(alert)
    send_telegram(f"*Alert • {sym}* `{ts}`\nMove: {move}")
    return "OK"

# ─── UI routes ──────────────────────────────────────────────────────
@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login_page"))
    kite_login_url = url_for("kite_auth") if not _token_available() else None
    date_str = request.args.get("date") or datetime.datetime.now(IST).strftime("%Y-%m-%d")
    return render_template("index.html",
                           alerts=load_alerts_for(date_str),
                           selected_date=date_str,
                           kite_login_url=kite_login_url)

@app.route("/login", methods=["GET", "POST"])
def login_page():
    if request.method == "POST":
        if request.form.get("username") == APP_USER and request.form.get("password") == APP_PASS:
            session["logged_in"] = True
            return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login_page"))

# ─── Run locally ────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)), debug=True)
