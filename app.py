
import os
import json
import datetime
from flask import Flask, request, render_template
from kiteconnect import KiteConnect
import math

app = Flask(__name__)

kite_api_key = os.environ.get("KITE_API_KEY")
kite_api_secret = os.environ.get("KITE_API_SECRET")
kite = KiteConnect(api_key=kite_api_key)
access_token_path = "access_token.txt"

# Load saved access token
if os.path.exists(access_token_path):
    with open(access_token_path, "r") as f:
        kite.set_access_token(f.read().strip())

alerts = []

def round_strike(price, step):
    return int(round(price / step) * step)

def is_index(symbol):
    return symbol.upper() in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]

def get_expiry_date(symbol):
    today = datetime.date.today()
    if is_index(symbol):
        # Weekly expiry: nearest Thursday
        days_ahead = 3 - today.weekday()
        if days_ahead < 0:
            days_ahead += 7
        expiry = today + datetime.timedelta(days=days_ahead)
    else:
        # Monthly expiry: last Thursday of the month
        next_month = today.replace(day=28) + datetime.timedelta(days=4)
        expiry = next_month - datetime.timedelta(days=next_month.weekday() + 2)
    return expiry.strftime("%Y-%m-%d")

def get_atm_option(symbol, option_type, expiry, spot):
    step = 50 if "BANKNIFTY" in symbol.upper() else 100 if "NIFTY" in symbol.upper() else 10
    atm = round_strike(spot, step)
    instrument_name = f"{symbol.upper()} {expiry} {atm} {option_type.upper()}"
    instruments = kite.instruments("NSE")
    for inst in instruments:
        if inst["tradingsymbol"].startswith(symbol.upper()) and inst["instrument_type"] == "OPT" and inst["strike"] == atm and inst["expiry"].strftime("%Y-%m-%d") == expiry and inst["segment"] == "NFO-OPT" and inst["name"] == symbol.upper() and inst["instrument_type"] == ("CE" if option_type == "CALL" else "PE"):
            return inst["tradingsymbol"]
    return None

def get_highest_volume_check(trading_symbol):
    try:
        end = datetime.datetime.now()
        start = datetime.datetime.combine(end.date(), datetime.time(9, 15))
        candles = kite.historical_data(trading_symbol, start, end, "5minute")
        if not candles:
            return False
        volumes = [c["volume"] for c in candles]
        return volumes[-1] == max(volumes)
    except Exception as e:
        print(f"Volume check error for {trading_symbol}: {e}")
        return False

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
        return "No symbol in alert", 400

    try:
        quote = kite.ltp(f"NSE:{symbol.upper()}")
        spot_price = quote[f"NSE:{symbol.upper()}"]["last_price"]
        expiry = get_expiry_date(symbol)
        put_symbol = get_atm_option(symbol, "PUT", expiry, spot_price)
        call_symbol = get_atm_option(symbol, "CALL", expiry, spot_price)

        put_check = get_highest_volume_check(f"NFO:{put_symbol}") if put_symbol else False
        call_check = get_highest_volume_check(f"NFO:{call_symbol}") if call_symbol else False

        result = {
            "symbol": symbol,
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "put_result": "✅" if put_check else "❌",
            "call_result": "✅" if call_check else "❌"
        }

        alerts.append(result)
        return "Processed", 200

    except Exception as e:
        print(f"Webhook error: {e}")
        return "Error", 500

if __name__ == "__main__":
    app.run(debug=True)
