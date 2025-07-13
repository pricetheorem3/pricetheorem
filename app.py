
import os
from flask import Flask, request, render_template
from kiteconnect import KiteConnect
import json
import datetime

app = Flask(__name__)

kite_api_key = os.environ.get("KITE_API_KEY")
kite = KiteConnect(api_key=kite_api_key)
access_token_path = "access_token.txt"

# Load access token if available
if os.path.exists(access_token_path):
    with open(access_token_path, "r") as f:
        kite.set_access_token(f.read().strip())

alerts = []

@app.route("/")
def index():
    return render_template("index.html", alerts=alerts, kite_api_key=kite_api_key)

@app.route("/login")
def login():
    request_token = request.args.get("request_token")
    data = kite.generate_session(request_token, api_secret=os.environ.get("KITE_API_SECRET"))
    kite.set_access_token(data["access_token"])
    with open(access_token_path, "w") as f:
        f.write(data["access_token"])
    return "Login successful. Access token saved."

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    symbol = data.get("symbol")
    time_received = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    result = {
        "symbol": symbol,
        "time": time_received,
        "put_result": "✅" if symbol and symbol[0].lower() < 'n' else "❌",
        "call_result": "✅" if symbol and symbol[0].lower() > 'n' else "❌"
    }

    alerts.append(result)
    return "Alert received", 200

if __name__ == "__main__":
    app.run(debug=True)
