
import os
import json
import datetime
from flask import Flask, request, render_template
from kiteconnect import KiteConnect

app = Flask(__name__)

kite_api_key = os.environ.get("KITE_API_KEY")
kite_api_secret = os.environ.get("KITE_API_SECRET")
kite = KiteConnect(api_key=kite_api_key)
access_token_path = "access_token.txt"

if os.path.exists(access_token_path):
    with open(access_token_path, "r") as f:
        kite.set_access_token(f.read().strip())

alerts = []

def get_expiry_date(symbol):
    today = datetime.date.today()
    if symbol.upper() in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]:
        days_ahead = 3 - today.weekday()
        if days_ahead < 0:
            days_ahead += 7
        expiry = today + datetime.timedelta(days=days_ahead)
    else:
        next_month = today.replace(day=28) + datetime.timedelta(days=4)
        expiry = next_month - datetime.timedelta(days=next_month.weekday() + 2)
    return expiry.strftime("%Y-%m-%d")

def get_strike_step(symbol):
    s = symbol.upper()
    if "BANKNIFTY" in s:
        return 100
    elif "NIFTY" in s:
        return 50
    else:
        return 10

def get_strike_range(spot, step):
    atm = round(spot / step) * step
    return [atm + step * i for i in range(-2, 3)]

def find_option(symbol, expiry, strike, option_type):
    instruments = kite.instruments("NFO")
    for inst in instruments:
        if (inst["tradingsymbol"].startswith(symbol.upper())
            and inst["instrument_type"] == ("CE" if option_type == "CALL" else "PE")
            and inst["strike"] == strike
            and inst["expiry"].strftime("%Y-%m-%d") == expiry):
            return inst["tradingsymbol"]
    return None

def check_option(symbol, is_put):
    try:
        end = datetime.datetime.now()
        start = datetime.datetime.combine(end.date(), datetime.time(9, 15))
        candles = kite.historical_data(symbol, start, end, "5minute")
        if not candles or len(candles) < 1:
            return "❌"
        volumes = [c["volume"] for c in candles]
        last = candles[-1]
        is_green = last["close"] > last["open"]
        is_red = last["close"] < last["open"]
        is_highest = last["volume"] == max(volumes)
        if is_highest and ((is_put and is_green) or (not is_put and is_red)):
            return "✅"
        return "❌"
    except Exception as e:
        print(f"Error checking {symbol}: {e}")
        return "❌"

@app.route("/")
def index():
    return render_template("index.html", alerts=alerts, kite_api_key=kite_api_key)

@app.route("/login")
def login():
    request_token = request.args.get("request_token")
    data = kite.generate_session(request_token, api_secret=kite_api_secret)
    kite.set_access_token(data["access_token"])
    with open(access_token_path, "w") as f:
        f.write(data["access_token"])
    return "Login successful. Access token saved."

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    symbol = data.get("symbol")
    if not symbol:
        return "Missing symbol", 400

    try:
        ltp_data = kite.ltp(f"NSE:{symbol.upper()}")
        quote = ltp_data[f"NSE:{symbol.upper()}"]
        spot_price = quote["last_price"]
        prev_close = quote["ohlc"]["close"]
        percent_change = ((spot_price - prev_close) / prev_close) * 100

        expiry = get_expiry_date(symbol)
        step = get_strike_step(symbol)
        strikes = get_strike_range(spot_price, step)

        put_results = []
        call_results = []

        for strike in strikes:
            pe_symbol = find_option(symbol, expiry, strike, "PUT")
            ce_symbol = find_option(symbol, expiry, strike, "CALL")
            put_results.append(check_option(f"NFO:{pe_symbol}", is_put=True) if pe_symbol else "❌")
            call_results.append(check_option(f"NFO:{ce_symbol}", is_put=False) if ce_symbol else "❌")

        result = {
            "symbol": symbol.upper(),
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ltp": f"₹{spot_price:.2f}",
            "pct_move": f"{percent_change:+.2f}%",
            "put_result": " ".join(put_results),
            "call_result": " ".join(call_results)
        }

        alerts.append(result)
        return "Processed", 200

    except Exception as e:
        print(f"Webhook error: {e}")
        return "Error", 500

if __name__ == "__main__":
    app.run(debug=True)
