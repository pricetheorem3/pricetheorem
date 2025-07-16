# app.py — screener + Trend/Flag + login + daily OI snapshot
# ───────────────────────────────────────────────────────────
import os, json, datetime, math, requests, threading, time, functools
from collections import defaultdict, deque
from flask import Flask, request, render_template, redirect, url_for, session
from kiteconnect import KiteConnect

# ─── TZ helpers ────────────────────────────────────────────
try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")
UTC = datetime.timezone.utc

# ─── Config (env‑vars) ────────────────────────────────────
WIDTH, GRACE_MINUTES, POLL_STEP = 2, 4, 20
RF_RATE  = float(os.getenv("RISK_FREE_RATE", 0.07))
DIV_YIELD= float(os.getenv("DIVIDEND_YIELD", 0.0))
DATA_DIR = os.getenv("DATA_DIR", ".")
ALERTS_FILE = os.path.join(DATA_DIR, "alerts.json")
TOKEN_FILE  = os.path.join(DATA_DIR, "access_token.txt")
OI915_FILE  = os.path.join(DATA_DIR, "oi_915.json")
WATCHLIST   = [s.strip().upper() for s in os.getenv("WATCHLIST", "").split(",") if s.strip()]

CFG = {
    'CE_big':3.0, 'PE_flat':1.0, 'PE_mult':2.0,
    'OI_rise':25000, 'skew_sigma':2.0, 'call_vol_req':1.5
}

# ─── Flask app ────────────────────────────────────────────
app = Flask(__name__)
app.secret_key   = os.getenv("FLASK_SECRET_KEY", "changeme")
KITE_API_KEY     = os.getenv("KITE_API_KEY")
KITE_API_SECRET  = os.getenv("KITE_API_SECRET")
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError("Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID")

# ─── Telegram helper ─────────────────────────────────────
def send_telegram(txt):
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id":TELEGRAM_CHAT_ID,"text":txt,"parse_mode":"Markdown"},
                      timeout=5)
    except Exception as e: print("Telegram:", e)

# ─── Kite helpers & instrument cache ─────────────────────
def get_kite():
    k=KiteConnect(api_key=KITE_API_KEY)
    if os.path.exists(TOKEN_FILE):
        k.set_access_token(open(TOKEN_FILE).read().strip())
    return k
INSTRUMENTS, INSTR_DATE=None,None
def get_instruments():
    global INSTRUMENTS, INSTR_DATE
    if INSTRUMENTS is None or INSTR_DATE!=datetime.datetime.now(IST).date():
        INSTRUMENTS=get_kite().instruments("NFO"); INSTR_DATE=datetime.datetime.now(IST).date()
    return INSTRUMENTS
def token_for_symbol(ts): return next((i["instrument_token"] for i in get_instruments()
                                       if i["tradingsymbol"]==ts), None)

# ─── Expiry helpers ──────────────────────────────────────
def next_expiry(sym):
    today=datetime.datetime.now(IST).date(); s=sym.upper()
    dates=sorted({i["expiry"] for i in get_instruments()
                  if i["instrument_type"] in {"PE","CE"} and
                     (i["name"]==s or i["tradingsymbol"].startswith(s))})
    return next((d for d in dates if d>=today), dates[-1])
def expiry_date(sym): return next_expiry(sym).strftime("%Y-%m-%d")

# ─── Option‑chain utilities ──────────────────────────────
def _matches(sym,exp): s=sym.upper(); return [i for i in get_instruments()
    if i["instrument_type"] in {"PE","CE"} and i["expiry"]==exp and
       (i["name"]==s or i["tradingsymbol"].startswith(s))]
def strikes_from_chain(sym,exp_str,spot):
    exp=datetime.datetime.strptime(exp_str,"%Y-%m-%d").date()
    strikes=sorted({i["strike"] for i in _matches(sym,exp)})
    if not strikes: return []
    atm=min(strikes,key=lambda k:abs(k-spot)); i=strikes.index(atm)
    return strikes[max(0,i-WIDTH):i+WIDTH+1]
def option_symbol(sym,exp_str,strike,kind):
    exp=datetime.datetime.strptime(exp_str,"%Y-%m-%d").date()
    for i in get_instruments():
        if i["instrument_type"]==("CE" if kind=="CALL" else "PE") and i["strike"]==strike and i["expiry"]==exp and \
            (i["name"]==sym.upper() or i["tradingsymbol"].startswith(sym.upper())):
            return i["tradingsymbol"]
    return None
@functools.lru_cache(maxsize=2048)
def quote_data(tsym):
    q=get_kite().quote(tsym)[tsym]
    return {"ltp":q["last_price"],"open":q["ohlc"]["open"],"oi":q.get("oi",0)}

# five‑minute volume ratio
def volume_ratio(tsym):
    tok=token_for_symbol(tsym); kite=get_kite()
    end=datetime.datetime.now(IST)
    cds=kite.historical_data(tok, end.replace(hour=9,minute=15,second=0,microsecond=0), end, "5minute")
    if not cds or len(cds)<4: return 0,0
    last=cds[-1]["volume"]; avg=sum(c["volume"] for c in cds[-4:-1])/3 or 1
    return last/avg, last

# volume‑spike tag
def check_option(tsym,is_put):
    tok=token_for_symbol(tsym); kite=get_kite()
    end=datetime.datetime.now(IST)
    cds=kite.historical_data(tok, end.replace(hour=9,minute=15,second=0,microsecond=0), end,"5minute") or []
    if not cds: return "❌"
    latest=cds[-1]
    if latest["volume"]!=max(c["volume"] for c in cds): return "❌"
    green=latest["close"]>latest["open"]; red=latest["close"]<latest["open"]
    return "✅" if ((is_put and green) or (not is_put and red)) else "❌"

# Black‑Scholes solver
def _bs_price(S,K,T,r,σ,q,cp):
    if σ<=0 or T<=0: return 0
    d1=(math.log(S/K)+(r-q+0.5*σ*σ)*T)/(σ*math.sqrt(T)); d2=d1-σ*math.sqrt(T)
    Φ=lambda x:.5*(1+math.erf(x/math.sqrt(2)))
    return cp*math.exp(-q*T)*S*Φ(cp*d1)-cp*math.exp(-r*T)*K*Φ(cp*d2)
def implied_vol(price,S,K,T,r,q,cp):
    lo,hi=1e-6,5
    for _ in range(100):
        mid=.5*(lo+hi)
        (hi if _bs_price(S,K,T,r,mid,q,cp)>price else lo).__setattr__('__iadd__',mid-mid)

# OI baseline snapshot (unchanged)
def load_oi_baseline():
    try: return json.load(open(OI915_FILE))
    except: return {}
def save_oi_baseline(d): json.dump(d,open(OI915_FILE,"w"))
def capture_all_oi():
    if not WATCHLIST: return
    kite=get_kite(); baseline={}
    deadline=datetime.datetime.now(IST).replace(hour=9,minute=15+GRACE_MINUTES,second=0)
    for sym_full in WATCHLIST:
        sym=sym_full.split(":")[-1]
        try: spot=kite.ltp(f"NSE:{sym}")[f"NSE:{sym}"]["last_price"]
        except: continue
        exp=expiry_date(sym); strikes=strikes_from_chain(sym,exp,spot)
        if not strikes: continue
        atm=min(strikes,key=lambda k:abs(k-spot))
        for st in strikes[strikes.index(atm)-2: strikes.index(atm)]:
            tsym=option_symbol(sym,exp,st,"PUT")
            if tsym and tsym not in baseline:
                try: oi=kite.quote(tsym)[tsym]["oi"]; baseline[tsym]=oi
                except: pass
        if datetime.datetime.now(IST)>deadline: break
    if baseline: save_oi_baseline(baseline)
def schedule_snapshot():
    def worker():
        while True:
            now=datetime.datetime.now(IST)
            nxt=now.replace(hour=9,minute=15,second=0,microsecond=0)
            if now>=nxt: nxt+=datetime.timedelta(days=1)
            time.sleep((nxt-now).total_seconds())
            capture_all_oi()
    threading.Thread(target=worker,daemon=True).start()

# rolling skew history
SKEW_HIST=defaultdict(lambda:deque(maxlen=20))

# alert storage
def today(): return datetime.datetime.now(IST).strftime("%Y-%m-%d")
def load_alerts_for(d): 
    try: return [a for a in json.load(open(ALERTS_FILE)) if a["time"].startswith(d)]
    except: return []
alerts_today=load_alerts_for(today())
def save_alert(a):
    try:
        data=load_alerts_for("1900-01-01"); 
        data=[x for x in data if not x["time"].startswith(a["time"][:10])]+[a]
        json.dump(data,open(ALERTS_FILE,"w"),indent=2)
    except: pass
    alerts_today.append(a)

# ─── ROUTES ───────────────────────────────────────────────
@app.route("/login", methods=["GET","POST"])
def login_page():
    if request.method=="POST":
        if (request.form.get("username")==os.getenv("APP_USERNAME","admin")
            and request.form.get("password")==os.getenv("APP_PASSWORD","price123")):
            session["logged_in"]=True; return redirect(url_for("index"))
        return render_template("login.html",error="Invalid credentials")
    return render_template("login.html")
@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login_page"))

@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login_page"))
    d=request.args.get("date",today())
    return render_template("index.html",alerts=load_alerts_for(d),
                           selected_date=d,kite_api_key=KITE_API_KEY)
@app.route("/history/<date_str>")
def history(date_str):
    if not session.get("logged_in"):
        return redirect(url_for("login_page"))
    return render_template("index.html",alerts=load_alerts_for(date_str),
                           selected_date=date_str,kite_api_key=KITE_API_KEY)

# ─── Webhook core (unchanged from previous full version) ──────────────────
#   … KEEP THE SAME BODY FROM THE LAST FILE (omitted here for brevity) …

# ─── Bootstrap ───────────────────────────────────────────
if __name__=="__main__":
    schedule_snapshot()
    app.run(debug=True)
