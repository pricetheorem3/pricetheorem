from flask import Flask, request, render_template, redirect, url_for
from kiteconnect import KiteConnect
import os
import datetime

app = Flask(__name__)

kite_api_key = os.getenv("KITE_API_KEY")
kite_api_secret = os.getenv("KITE_API_SECRET")

alerts = []

def get_kite():
    kite = KiteConnect(api_key=kite_api_key)
    access_token_path = "access_token.txt"
    if os.path.exists(access_token_path):
        with open(access_token_path, "r") as f:
            access_token = f.read().strip()
            kite.set_access_token(access_token)
    else:
        print("❌ access_token.txt not found.")
    return kite

@app.route("/")
def index():
    return render_template("index.html", alerts=alerts, kite_api_key=kite_api_key)

@app.route("/login")
def login():
    kite = KiteConnect(api_key=kite_api_key)
    login_url = kite.login_url()
    return redirect(login_url)

@app.route("/login/callback")
def login_callback():
    request_token = request.args.get("request_token")
    if not request_token:
        return "Login failed: No request_token provided"

    try:
        kite = KiteConnect(api_key=kite_api_key)
        data = kite.generate_session(request_token, api_secret=kite_api_secret)
        access_token = data["access_token"]

        with open("access_token.txt", "w") as f:
            f.write(access_token)

        print("Access token generated and set successfully.")
        return redirect(url_for("index"))

    except Exception as e:
        print(f"Login callback error: {e}")
        return "Login failed during token generation"

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.json
        print("Webhook received:", data)

        symbol = data.get("symbol")
        ltp = float(data.get("ltp"))
        open_price = float(data.get("open"))
        time = data.get("time")

        pct_move = round(((ltp - open_price) / open_price) * 100, 2)

        kite = get_kite()

        # --- Option logic ---
        instrument_data = kite.ltp([f"NSE:{symbol}"])
        stock_price = instrument_data[f"NSE:{symbol}"]["last_price"]
        strike = round(stock_price / 10) * 10

        today = datetime.date.today().strftime("%y%b").upper()
        expiry = f"{today}29" if symbol not in ["NIFTY", "BANKNIFTY"] else f"{today}18"

        option_prefix = "NSE:" if symbol not in ["NIFTY", "BANKNIFTY"] else "NFO:"

        put_symbol = f"{option_prefix}{symbol}{expiry}{strike}PE"
        call_symbol = f"{option_prefix}{symbol}{expiry}{strike}CE"

        put_data = kite.historical_data(kite.ltp([put_symbol])[put_symbol]['instrument_token'], "09:15", "15:30", "5minute")
        call_data = kite.historical_data(kite.ltp([call_symbol])[call_symbol]['instrument_token'], "09:15", "15:30", "5minute")

        put_volumes = [bar['volume'] for bar in put_data if bar['close'] > bar['open']]
        call_volumes = [bar['volume'] for bar in call_data if bar['close'] < bar['open']]

        put_result = "✅" if put_volumes and put_volumes[-1] == max(put_volumes) else "❌"
        call_result = "✅" if call_volumes and call_volumes[-1] == max(call_volumes) else "❌"

        alerts.append({
            "symbol": symbol,
            "time": time,
            "ltp": ltp,
            "pct_move": pct_move,
            "put_result": put_result,
            "call_result": call_result
        })

        return "success"

    except Exception as e:
        print(f"Webhook error: {e}")
        return "error", 500


