# app.py — full screener + Trend/Flag engine
# ─────────────────────────────────────────────────────────────────────────────
import os, json, datetime, math, requests, threading, time, functools
from collections import defaultdict, deque
from flask import Flask, request, render_template, redirect, url_for, session
from kiteconnect import KiteConnect

# ─── Time zones ──────────────────────────────────────────────────────────────
try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")
UTC = datetime.timezone.utc

# ─── Config & env-vars (tweak via Render dashboard) ─────────────────────────
WIDTH             = 2
RF_RATE           = float(os.getenv("RISK_FREE_RATE", 0.07))
DIV_YIELD         = float(os.getenv("DIVIDEND_YIELD", 0.0))
GRACE_MINUTES     = 4
POLL_STEP         = 20
DATA_DIR          = os.getenv("DATA_DIR", ".")
ALERTS_FILE       = os.path.join(DATA_DIR, "alerts.json")
TOKEN_FILE        = os.path.join(DATA_DIR, "access_token.txt")
OI915_FILE        = os.path.join(DATA_DIR, "oi_915.json")
WATCHLIST         = [s.strip().upper() for s in
                     os.getenv("WATCHLIST", "").split(",") if s.strip()]

# Trend/Flag thresholds (edit as needed)
CFG = {
    'CE_big'       : 3.0,      # “large” ΔCE
    'PE_flat'      : 1.0,      # |ΔPE| < 1 == flat
    'PE_mult'      : 2.0,      # PE collapse test (ΔPE ≤ –2×|ΔCE|)
    'OI_rise'      : 25000,    # put‑OI rise red‑flag
    'skew_sigma'   : 2.0,      # skew jump z‑score
    'call_vol_req' : 1.5       # call volume ratio good/bad
}

# ─── Flask & secrets ────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key    = os.getenv("FLASK_SECRET_KEY", "changeme")
KITE_API_KEY      = os.getenv("KITE_API_KEY")
KITE_API_SECRET   = os.getenv("KITE_API_SECRET")
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")
if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError("Set TELEGRAM_TOKEN and TELEGRAM_CHAT_ID")

# ─── Telegram helper ───────────────────────────────────────────────────────
def send_telegram(txt):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": txt, "parse_mode": "Markdown"},
            timeout=5,
        )
    except Exception as e:
        print("Telegram:", e)

# ─── Kite helpers / instrument cache ───────────────────────────────────────
def get_kite():
    k = KiteConnect(api_key=KITE_API_KEY)
    if os.path.exists(TOKEN_FILE):
        k.set_access_token(open(TOKEN_FILE).read().strip())
    return k

INSTRUMENTS, INSTR_DATE = None, None
def get_instruments():
    global INSTRUMENTS, INSTR_DATE
    today = datetime.datetime.now(IST).date()
    if INSTRUMENTS is None or INSTR_DATE != today:
        INSTRUMENTS = get_kite().instruments("NFO")
        INSTR_DATE  = today
    return INSTRUMENTS

def token_for_symbol(tsym):
    for inst in get_instruments():
        if inst["tradingsymbol"] == tsym:
            return inst["instrument_token"]
    return None

# ─── Expiry helpers ────────────────────────────────────────────────────────
def next_expiry(symbol):
    s, today = symbol.upper(), datetime.datetime.now(IST).date()
    dates = sorted({i["expiry"] for i in get_instruments()
                    if i["instrument_type"] in {"PE","CE"} and
                       (i["name"] == s or i["tradingsymbol"].startswith(s))})
    for d in dates:
        if d >= today: return d
    return dates[-1]
def expiry_date(symbol): return next_expiry(symbol).strftime("%Y-%m-%d")

# ─── Option‑chain helpers ──────────────────────────────────────────────────
def _matches(sym, exp):
    s = sym.upper()
    return [i for i in get_instruments()
            if i["instrument_type"] in {"PE","CE"} and i["expiry"] == exp and
               (i["name"] == s or i["tradingsymbol"].startswith(s))]
def strikes_from_chain(sym, exp_str, spot):
    exp = datetime.datetime.strptime(exp_str,"%Y-%m-%d").date()
    inst = _matches(sym, exp)
    if not inst: return []
    strikes = sorted({i["strike"] for i in inst})
    atm = min(strikes, key=lambda k: abs(k-spot))
    i   = strikes.index(atm)
    return strikes[max(0,i-WIDTH): i+WIDTH+1]
def option_symbol(sym, exp_str, strike, kind):
    exp = datetime.datetime.strptime(exp_str,"%Y-%m-%d").date()
    for i in get_instruments():
        if i["instrument_type"] == ("CE" if kind=="CALL" else "PE") \
           and i["strike"] == strike and i["expiry"] == exp and \
           (i["name"] == sym.upper() or i["tradingsymbol"].startswith(sym.upper())):
            return i["tradingsymbol"]
    return None
@functools.lru_cache(maxsize=2048)
def quote_data(tsym):
    q = get_kite().quote(tsym)[tsym]
    return {"ltp": q["last_price"], "open": q["ohlc"]["open"], "oi": q.get("oi",0)}

# ─── Five‑minute volume helpers ────────────────────────────────────────────
def volume_ratio(tsym):
    tok = token_for_symbol(tsym)
    if tok is None: return 0, 0
    kite = get_kite()
    end   = datetime.datetime.now(IST)
    start = datetime.datetime.combine(end.date(), datetime.time(9,15,tzinfo=IST))
    cds   = kite.historical_data(tok, start, end, "5minute") or []
    if len(cds) < 4: return 0, 0
    last = cds[-1]["volume"]
    avg  = sum(c["volume"] for c in cds[-4:-1]) / 3
    return last / avg if avg else 0, last

def check_option(tsym, is_put):
    tok = token_for_symbol(tsym)
    if tok is None: return "❌"
    kite = get_kite()
    end   = datetime.datetime.now(IST)
    start = datetime.datetime.combine(end.date(), datetime.time(9,15,tzinfo=IST))
    cds   = kite.historical_data(tok, start, end, "5minute") or []
    if not cds: return "❌"
    latest = cds[-1]
    if latest["volume"] != max(c["volume"] for c in cds): return "❌"
    green = latest["close"] > latest["open"]
    red   = latest["close"]  < latest["open"]
    return "✅" if ((is_put and green) or (not is_put and red)) else "❌"

# ─── Black‑Scholes IV solver ───────────────────────────────────────────────
def _bs_price(S,K,T,r,σ,q,cp):
    if σ<=0 or T<=0: return 0
    d1 = (math.log(S/K)+(r-q+0.5*σ*σ)*T)/(σ*math.sqrt(T))
    d2 = d1-σ*math.sqrt(T)
    Φ = lambda x: 0.5*(1+math.erf(x/math.sqrt(2)))
    return cp*math.exp(-q*T)*S*Φ(cp*d1)-cp*math.exp(-r*T)*K*Φ(cp*d2)
def implied_vol(price,S,K,T,r,q,cp):
    lo, hi = 1e-6, 5.0
    for _ in range(100):
        mid = 0.5*(lo+hi)
        if abs(_bs_price(S,K,T,r,mid,q,cp)-price) < 1e-4: return mid
        if _bs_price(S,K,T,r,mid,q,cp) > price: hi = mid
        else: lo = mid
    return mid

# ─── OI baseline snapshot (unchanged) ──────────────────────────────────────
def load_oi_baseline():
    if os.path.exists(OI915_FILE):
        try: return json.load(open(OI915_FILE))
        except Exception: pass
    return {}
def save_oi_baseline(d): json.dump(d, open(OI915_FILE,"w"))

def capture_all_oi():
    if not WATCHLIST: return
    kite = get_kite()
    baseline, missing = {}, [s for s in WATCHLIST]
    deadline = datetime.datetime.now(IST).replace(
        hour=9, minute=15+GRACE_MINUTES, second=0, microsecond=0)
    while missing and datetime.datetime.now(IST) < deadline:
        for sym_full in missing[:]:
            sym = sym_full.split(":")[-1]
            try: spot = kite.ltp(f"NSE:{sym}")[f"NSE:{sym}"]["last_price"]
            except Exception: continue
            exp  = expiry_date(sym)
            strikes = strikes_from_chain(sym, exp, spot)
            if not strikes: continue
            atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i]-spot))
            for st in strikes[max(0,atm_idx-2): atm_idx]:
                tsym = option_symbol(sym, exp, st, "PUT")
                if not tsym or tsym in baseline: continue
                try:
                    oi_now = kite.quote(tsym)[tsym]["oi"]
                    if oi_now: baseline[tsym] = oi_now
                except Exception: pass
            if len([k for k in baseline if k.startswith(sym)]) >= 2:
                missing.remove(sym_full)
        if missing: time.sleep(POLL_STEP)
    if baseline: save_oi_baseline(baseline)

def schedule_snapshot():
    def worker():
        while True:
            now = datetime.datetime.now(IST)
            nxt = now.replace(hour=9, minute=15, second=0, microsecond=0)
            if now >= nxt: nxt += datetime.timedelta(days=1)
            time.sleep((nxt-now).total_seconds())
            try: capture_all_oi(); print("09:15 OI baseline captured.")
            except Exception as e: print("Snapshot thread:", e)
    threading.Thread(target=worker, daemon=True).start()

# ─── Rolling skew history for z‑score ──────────────────────────────────────
SKEW_HIST = defaultdict(lambda: deque(maxlen=20))

# ─── Alert persistence & historical loader ────────────────────────────────
def today(): return datetime.datetime.now(IST).strftime("%Y-%m-%d")
def load_alerts_for(date_str):
    if not os.path.exists(ALERTS_FILE): return []
    try: return [a for a in json.load(open(ALERTS_FILE))
                if a["time"].startswith(date_str)]
    except Exception: return []
alerts_today = load_alerts_for(today())
def save_alert(a):
    hist = load_alerts_for(a["time"][:10]) + [a]
    all_  = load_alerts_for("1900-01-01")
    merged = [x for x in all_ if not x["time"].startswith(a["time"][:10])] + hist
    json.dump(merged, open(ALERTS_FILE,"w"), indent=2)
    alerts_today.append(a)

# ─── Flask routes (index/history; login omitted for brevity) ───────────────
@app.route("/")
def index():
    if not session.get("logged_in"):
        return redirect(url_for("login_page"))
    date_req = request.args.get("date", today())
    return render_template("index.html",
                           alerts=load_alerts_for(date_req),
                           selected_date=date_req,
                           kite_api_key=KITE_API_KEY)

@app.route("/history/<date_str>")
def history(date_str):
    if not session.get("logged_in"):
        return redirect(url_for("login_page"))
    return render_template("index.html",
                           alerts=load_alerts_for(date_str),
                           selected_date=date_str,
                           kite_api_key=KITE_API_KEY)

# ─── Webhook core ─────────────────────────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    p = request.json or {}
    symbol = p.get("symbol")
    if not symbol: return "Missing symbol",400

    # --- parse trigger time ---
    trg = p.get("trigger_time")
    if trg:
        try: trig_dt=datetime.datetime.fromtimestamp(int(trg),UTC).astimezone(IST)
        except Exception:
            try:
                iso=datetime.datetime.fromisoformat(trg.rstrip("Z"))
                if iso.tzinfo is None: iso=iso.replace(tzinfo=UTC)
                trig_dt=iso.astimezone(IST)
            except Exception:
                trig_dt=datetime.datetime.now(IST)
    else: trig_dt=datetime.datetime.now(IST)

    kite=get_kite()
    try:
        ltp = kite.ltp(f"NSE:{symbol.upper()}")[f"NSE:{symbol.upper()}"]["last_price"]
        exp = expiry_date(symbol)
        strikes = strikes_from_chain(symbol, exp, ltp)
        if not strikes:
            put_result = call_result = "No option chain"
            ΔCE = ΔPE = skew = ΔOI_put = call_vol_ratio = "–"
        else:
            baseline = load_oi_baseline()
            atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i]-ltp))
            delta_strikes = strikes[max(0, atm_idx-1): atm_idx+2]   # ATM ±1

            put_tags, call_tags = [], []
            ce_moves, pe_moves = [], []
            put_oi_now, put_oi_915 = [], []
            call_vol_ratios = []

            for st in strikes:
                ts_pe = option_symbol(symbol, exp, st, "PUT")
                ts_ce = option_symbol(symbol, exp, st, "CALL")
                put_tags.append (f"{st}{check_option(ts_pe, True)  if ts_pe else '❌'}")
                call_tags.append(f"{st}{check_option(ts_ce, False) if ts_ce else '❌'}")

                if st in delta_strikes:
                    if ts_ce:
                        q = quote_data(ts_ce); ce_moves.append(q["ltp"]-q["open"])
                        ratio, _ = volume_ratio(ts_ce)
                        call_vol_ratios.append(ratio)
                    if ts_pe:
                        q = quote_data(ts_pe); pe_moves.append(q["ltp"]-q["open"])

                if ts_pe and st in strikes[max(0,atm_idx-2):atm_idx]:
                    q = quote_data(ts_pe)
                    put_oi_now.append(q["oi"])
                    if ts_pe in baseline: put_oi_915.append(baseline[ts_pe])

            ΔCE      = round(sum(ce_moves),2) if ce_moves else 0.0
            ΔPE      = round(sum(pe_moves),2) if pe_moves else 0.0
            ΔOI_put  = (sum(put_oi_now)-sum(put_oi_915)) if put_oi_915 else 0
            call_vol_ratio = round(max(call_vol_ratios),2) if call_vol_ratios else 0

            # --- IV skew ---
            atm_strike = strikes[atm_idx]
            T = (datetime.datetime.strptime(exp,"%Y-%m-%d").date() -
                 datetime.datetime.now(IST).date()).days/365
            iv_call = iv_put = None
            ts_atm_ce = option_symbol(symbol, exp, atm_strike, "CALL")
            ts_atm_pe = option_symbol(symbol, exp, atm_strike, "PUT")
            if ts_atm_ce:
                q = quote_data(ts_atm_ce)
                iv_call = implied_vol(q["ltp"], ltp, atm_strike, T,
                                      RF_RATE, DIV_YIELD, 1)
            if ts_atm_pe:
                q = quote_data(ts_atm_pe)
                iv_put = implied_vol(q["ltp"], ltp, atm_strike, T,
                                     RF_RATE, DIV_YIELD, -1)
            skew = round(100*(iv_call-iv_put),2) if iv_call and iv_put else 0.0

            # --- rolling skew stats ---
            hist = SKEW_HIST[symbol]
            hist.append(skew)
            mu = sum(hist)/len(hist) if hist else 0
            std= (sum((x-mu)**2 for x in hist)/len(hist))**0.5 if len(hist)>1 else 0.1
            skew_jump = (skew-mu)/std if std else 0

            # --- Trend / Flag checks ---
            flags=[]
            if skew_jump > CFG['skew_sigma'] and ΔCE > CFG['CE_big'] and abs(ΔPE) < CFG['PE_flat']:
                flags.append("IV_PUMP")
            if call_vol_ratio < CFG['call_vol_req'] and ΔCE > CFG['CE_big']:
                flags.append("LOW_VOL")
            if ΔOI_put > CFG['OI_rise']:
                flags.append("PUT_OI_RISE")

            # good tests for UP
            put_collapse_good = ΔPE <= -CFG['PE_mult']*abs(ΔCE)
            oi_good           = ΔOI_put <= 0
            vol_good          = call_vol_ratio >= CFG['call_vol_req']

            if abs(ΔCE) < 0.5:
                trend="SIDEWAYS"
            elif ΔCE > 0:
                if put_collapse_good and oi_good and vol_good and not flags:
                    trend="CONFIRMED_UP"
                elif flags:
                    trend="FAKE_UP"
                else:
                    trend="UNCONFIRMED"
            else:  # ΔCE < 0  (mirror logic for down‑move)
                put_buy_good = ΔPE >= CFG['PE_mult']*abs(ΔCE)
                oi_rise_good = ΔOI_put >= 0
                vol_good     = call_vol_ratio >= CFG['call_vol_req']
                if put_buy_good and oi_rise_good and vol_good and not flags:
                    trend="CONFIRMED_DOWN"
                elif flags:
                    trend="FAKE_DOWN"
                else:
                    trend="UNCONFIRMED"

            put_result  = "  ".join(put_tags)
            call_result = "  ".join(call_tags)
            flag_out    = ",".join(flags) if flags else "OK"
        # end strikes
        alert = {
            "symbol":symbol.upper(),
            "time":trig_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "ltp":f"₹{ltp:.2f}",
            "ΔCE":ΔCE,"ΔPE":ΔPE,"Skew":skew,"ΔOI_PUT":ΔOI_put,
            "call_vol":call_vol_ratio,
            "trend":trend,"flag":flag_out,
            "put_result":put_result,"call_result":call_result
        }
        save_alert(alert)

        # --- Telegram push ---
        msg=(f"*Signal* {'📈' if 'UP' in trend else '📉' if 'DOWN' in trend else '⚠️'}  ({trend})\n"
             f"Symbol : `{alert['symbol']}`\n"
             f"Time   : {alert['time']}\n"
             f"LTP    : {alert['ltp']}\n\n"
             f"ΔCE    : {ΔCE}\nΔPE    : {ΔPE}\n"
             f"Skew   : {skew}\nΔOIᵖ   : {ΔOI_put}\n"
             f"CallVol: {call_vol_ratio}×\n\n"
             f"PUT    : {put_result}\nCALL   : {call_result}\n"
             f"Flag   : {flag_out}")
        send_telegram(msg)
        return "OK",200
    except Exception as e:
        print("Webhook:",e); return "Error",500

# ─── Bootstrap ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    schedule_snapshot()
    app.run(debug=True)
