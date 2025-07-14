from flask import Flask, request, render_template, redirect
from kiteconnect import KiteConnect
import requests
import json
import os
from datetime import datetime

app = Flask(__name__)

kite_api_key = os.environ.get("KITE_API_KEY")
kite_api_secret = os.environ.get("KITE_API_SECRET")
kite = KiteConnect(api_key=kite_api_key)

access_token_path = "access_token.txt"

alerts = []

# Load token if available
if os.path.exists(access_token_path):
    with open(access_token_path, "r") as f:
        kite.set_access_token(f.read().strip())

@app.route("/")
def home():
    return render_template("index.html", alerts=alerts, kite_api_key=kite_api_key)

@app.route("/login")
def login():
    return redirect(kite.login_url())

@app.route("/login/callback")
def login_callback():
    request_token = request.args.get("request_token")
    data = kite.generate_session(request_token, api_secret=kite_api_secret)
    kite.set_access_token(data["access_token"])
    with open(access_token_path, "w") as f:
        f.write(data["access_token"])
    print("Access token generated and set successfully.")
    return redirect("/")

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.json
        symbol = data.get("symbol")
        if not symbol:
            return "Missing symbol", 400

        # Fetch LTP
        quote = kite.ltp(f"NSE:{symbol}")
        spot_price = quote[f"NSE:{symbol}"]["last_price"]

        # Strike price logic (nearest 50)
        strike = round(spot_price / 50) * 50

        # Fetch option tokens
        instruments = kite.instruments("NSE")
        today = datetime.now().date()
        expiry = max(i["expiry"] for i in instruments if i["name"] == symbol and i["instrument_type"] == "OPTSTK")

        strikes = [strike - 100, strike - 50, strike, strike + 50, strike + 100]
        call_tokens, put_tokens = {}, {}

        for inst in instruments:
            if inst["name"] == symbol and inst["expiry"] == expiry:
                if inst["strike"] in strikes:
                    if inst["instrument_type"] == "CE":
                        call_tokens[inst["strike"]] = inst["instrument_token"]
                    elif inst["instrument_type"] == "PE":
                        put_tokens[inst["strike"]] = inst["instrument_token"]

        headers = {"Authorization": f"token {kite_api_key}:{kite.access_token}"}

        def get_candle_data(token):
            url = f"https://api.kite.trade/instruments/historical/{token}/5minute?from={today}T09:15:00&to={today}T15:30:00&interval=5minute"
            res = requests.get(url, headers=headers)
            candles = res.json().get("data", {}).get("candles", [])
            return candles

        def is_high_volume(candles, is_put=True):
            if not candles:
                return "❌"
            volumes = [c[5] for c in candles]
            max_vol = max(volumes)
            latest = candles[-1]
            is_green = latest[4] > latest[1]
            is_red = latest[4] < latest[1]
            if is_put and is_green and latest[5] == max_vol:
                return "✅"
            if not is_put and is_red and latest[5] == max_vol:
                return "✅"
            return "❌"

        put_result = is_high_volume(get_candle_data(put_tokens.get(strike)), is_put=True)
        call_result = is_high_volume(get_candle_data(call_tokens.get(strike)), is_put=False)

        alert = {
            "symbol": symbol,
            "time": datetime.now().strftime("%H:%M:%S"),
            "ltp": spot_price,
            "pct_move": "",  # Removed previous close logic
            "put_result": put_result,
            "call_result": call_result
        }

        alerts.append(alert)
        if len(alerts) > 20:
            alerts.pop(0)

        print(f"Processed alert for {symbol}")
        return "OK", 200

    except Exception as e:
        print("Webhook error:", str(e))
        return "Error", 500

if __name__ == "__main__":
    app.run(debug=True)
