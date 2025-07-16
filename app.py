# app.py — iGOT option screener (SQLite + full metrics + IV‑pump logic)
# ────────────────────────────────────────────────────────────────────
import os, json, math, time, datetime, threading, logging, sqlite3, requests
from zoneinfo import ZoneInfo
from flask import Flask, request, render_template, redirect, url_for, session
from kiteconnect import KiteConnect
from db import init_db, DB_FILE               # ← make sure db.py is beside this file

# ─── basic --------------------------------------------------------------------
IST        = ZoneInfo("Asia/Kolkata")
IV_FILE    = "iv_915.json"                    # 9 : 15 ATM IV baseline snapshot
WATCHLIST  = [s.strip().split(":")[-1] for s in os.getenv("WATCHLIST","").split(",") if s.strip()]
app        = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY","changeme")

KITE_API_KEY    = os.getenv("KITE_API_KEY")
KITE_API_SECRET = os.getenv("KITE_API_SECRET")
TELEGRAM_TOKEN  = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID= os.getenv("TELEGRAM_CHAT_ID")

init_db()
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ─── helpers ------------------------------------------------------------------
def get_kite() -> KiteConnect:
    k = KiteConnect(api_key=KITE_API_KEY)
    if os.path.exists("access_token.txt"):
        k.set_access_token(open("access_token.txt").read().strip())
    return k

def send_telegram(msg:str):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT_ID): return
    try:
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                      data={"chat_id":TELEGRAM_CHAT_ID,"text":msg,"parse_mode":"Markdown"},timeout=5)
    except Exception as e: log.warning("Telegram error: %s",e)

def save_alert(a:dict):
    with sqlite3.connect(DB_FILE) as c:
        c.execute("""
        INSERT INTO alerts (symbol,time,move,ltp,dce,dpe,skew,doi_put,
                            call_vol,trend,flag,ivd_ce,ivd_pe,iv_flag,
                            call_result,put_result)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,(a["symbol"],a["time"],a["move"],a["ltp"],a["ΔCE"],a["ΔPE"],
             a["Skew"],a["ΔOI_PUT"],a["call_vol"],a["trend"],a["flag"],
             a["IVΔ_CE"],a["IVΔ_PE"],a["iv_flag"],
             a["call_result"],a["put_result"]))

def load_alerts_for(day:str):
    with sqlite3.connect(DB_FILE) as c:
        cur=c.cursor()
        cur.execute("SELECT * FROM alerts WHERE time LIKE ? ORDER BY time DESC",(f"{day}%",))
        cols=[d[0] for d in cur.description]
        return [dict(zip(cols,row)) for row in cur.fetchall()]

# ─── BS / IV ------------------------------------------------------------------
def _bs_price(S,K,T,r,σ,q,cp):
    if σ<=0 or T<=0: return 0
    d1=(math.log(S/K)+(r-q+0.5*σ*σ)*T)/(σ*math.sqrt(T))
    d2=d1-σ*math.sqrt(T)
    Φ=lambda x:0.5*(1+math.erf(x/math.sqrt(2)))
    return cp*(S*math.exp(-q*T)*Φ(cp*d1)-K*math.exp(-r*T)*Φ(cp*d2))

def implied_vol(price,S,K,T,r=0.07,q=0.0,cp=1):
    lo,hi=1e-6,5
    for _ in range(60):
        mid=(lo+hi)/2
        if _bs_price(S,K,T,r,mid,q,cp)>price: hi=mid
        else: lo=mid
    return round((lo+hi)/2,4)

# ─── 9 : 15 IV baseline -------------------------------------------------------
def capture_iv_snapshot():
    if not WATCHLIST: return
    kite=get_kite(); now=datetime.datetime.now(IST); ivs={}
    for sym in WATCHLIST:
        try:
            inst=[i for i in kite.instruments("NFO") if i["name"]==sym]
            exp=sorted({i["expiry"] for i in inst})[0].strftime("%Y-%m-%d")
            spot=kite.ltp(f"NSE:{sym}")[f"NSE:{sym}"]["last_price"]
            strikes=sorted({i["strike"] for i in inst if i["expiry"].strftime("%Y-%m-%d")==exp})
            atm=min(strikes,key=lambda k:abs(k-spot))
            fmt=lambda k,t:f"{sym}{exp[2:4]}{exp[5:7]}{int(k):05d}{t}"
            for t,cp in (("CE",1),("PE",-1)):
                ts=fmt(atm,t); q=kite.quote(ts)[ts]
                K=float(ts[-7:-2]); T=(datetime.datetime.strptime(exp,"%Y-%m-%d")-now.date()).days/365
                ivs[f"{sym}_{t}"]=implied_vol(q["last_price"],spot,K,T,cp=cp)
        except: pass
    json.dump(ivs,open(IV_FILE,"w"))
    log.info("IV baseline captured for %d symbols",len(ivs))

def schedule_iv_job():
    def worker():
        while True:
            now=datetime.datetime.now(IST)
            tgt=now.replace(hour=9,minute=15,second=0,microsecond=0)
            if now>=tgt: tgt+=datetime.timedelta(days=1)
            time.sleep((tgt-now).total_seconds())
            capture_iv_snapshot()
    threading.Thread(target=worker,daemon=True).start()

schedule_iv_job()

# ─── Webhook ------------------------------------------------------------------
@app.route("/webhook",methods=["POST"])
def webhook():
    data=request.get_json(force=True)
    if not data or "symbol" not in data: return "Bad payload",400
    sym=data["symbol"].upper(); move=data.get("move","")
    now=datetime.datetime.now(IST); ts=now.strftime("%Y-%m-%d %H:%M:%S")
    kite=get_kite(); spot=kite.ltp(f"NSE:{sym}")[f"NSE:{sym}"]["last_price"]

    alert={"symbol":sym,"time":ts,"move":move,"ltp":spot}

    try:
        inst=[i for i in kite.instruments("NFO") if i["name"]==sym]
        exp=sorted({i["expiry"] for i in inst})[0].strftime("%Y-%m-%d")
        strikes=sorted({i["strike"] for i in inst if i["expiry"].strftime("%Y-%m-%d")==exp})
        atm=min(strikes,key=lambda k:abs(k-spot))
        fmt=lambda k,t:f"{sym}{exp[2:4]}{exp[5:7]}{int(k):05d}{t}"

        # Δ premium sums
        dce=dpe=0
        for k in [x for x in strikes if x>=atm][:3]:
            q=kite.quote(fmt(k,"CE"))[fmt(k,"CE")]
            dce+=q["last_price"]-q["ohlc"]["open"]
        for k in [x for x in strikes if x<=atm][-3:]:
            q=kite.quote(fmt(k,"PE"))[fmt(k,"PE")]
            dpe+=q["last_price"]-q["ohlc"]["open"]
        alert["ΔCE"]=round(dce,2); alert["ΔPE"]=round(dpe,2)

        ce_atm=fmt(atm,"CE"); pe_atm=fmt(atm,"PE")
        def iv_now(ts,cp):
            q=kite.quote(ts)[ts]; K=float(ts[-7:-2])
            T=(datetime.datetime.strptime(exp,"%Y-%m-%d")-now.date()).days/365
            return implied_vol(q["last_price"],spot,K,T,cp=cp)
        iv_ce,iv_pe=iv_now(ce_atm,1),iv_now(pe_atm,-1)
        alert["Skew"]=round(iv_ce-iv_pe,4)

        alert["call_vol"]=round(kite.quote(ce_atm)[ce_atm]["volume"]/1000,2)
        alert["ΔOI_PUT"]=kite.quote(pe_atm)[pe_atm]["oi"]        # simple live value

        alert["call_result"]="✅" if kite.quote(ce_atm)[ce_atm]["last_price"]>kite.quote(ce_atm)[ce_atm]["ohlc"]["open"] else "❌"
        alert["put_result"]="✅" if kite.quote(pe_atm)[pe_atm]["last_price"]>kite.quote(pe_atm)[pe_atm]["ohlc"]["open"] else "❌"

        alert["trend"]="Bullish" if dce>abs(dpe) else "Bearish" if dpe>abs(dce) else "Flat"
        alert["flag"]="Flat PE" if alert["ΔPE"]<0 else ("Strong CE" if alert["ΔCE"]>3 else "")

        baseline=json.load(open(IV_FILE)) if os.path.exists(IV_FILE) else {}
        iv0_ce=baseline.get(f"{sym}_CE",iv_ce); iv0_pe=baseline.get(f"{sym}_PE",iv_pe)
        alert["IVΔ_CE"]=round(iv_ce-iv0_ce,4)
        alert["IVΔ_PE"]=round(iv_pe-iv0_pe,4)
        thr=0.03
        alert["iv_flag"]="IV Pump" if max(alert["IVΔ_CE"],alert["IVΔ_PE"])>=thr else \
                         "IV Crush" if min(alert["IVΔ_CE"],alert["IVΔ_PE"])<=-thr else ""

    except Exception as e:
        log.warning("calc error: %s",e)
        alert["error"]=str(e)

    save_alert(alert)
    send_telegram(f"*Alert • {sym}* `{ts}`\nMove: {move}")
    return "OK"

# ─── UI -----------------------------------------------------------------------
@app.route("/")
def index():
    if not session.get("logged_in"): return redirect(url_for("login_page"))
    d=request.args.get("date") or datetime.datetime.now(IST).strftime("%Y-%m-%d")
    return render_template("index.html",alerts=load_alerts_for(d),selected_date=d)

@app.route("/login",methods=["GET","POST"])
def login_page():
    if request.method=="POST":
        if (request.form.get("username")==os.getenv("APP_USERNAME","admin")
            and request.form.get("password")==os.getenv("APP_PASSWORD","price123")):
            session["logged_in"]=True; return redirect(url_for("index"))
    return render_template("login.html")

@app.route("/logout")
def logout(): session.clear(); return redirect(url_for("login_page"))

if __name__=="__main__":
    app.run(debug=True)
