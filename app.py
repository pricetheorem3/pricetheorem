from flask import Flask, request, render_template, jsonify
import os, datetime
app = Flask(__name__)
alerts = []

@app.route("/")
def index():
    return "Welcome to PriceTheorem"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.get_json()
    stock = data.get("symbol", "UNKNOWN")
    time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    alerts.append({"stock": stock, "time": time})
    return jsonify({"status": "received", "stock": stock})

@app.route("/alerts")
def show_alerts():
    return render_template("alerts.html", alerts=alerts)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
