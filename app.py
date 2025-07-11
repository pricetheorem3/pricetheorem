
from flask import Flask, redirect, request, render_template, session
import os
import requests

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "your-secret-key")

API_KEY = os.environ.get("KITE_API_KEY", "")
API_SECRET = os.environ.get("KITE_API_SECRET", "")

@app.route("/")
def home():
    if "access_token" in session:
        return render_template("token.html", token=session["access_token"])
    return render_template("home.html", api_key=API_KEY)

@app.route("/kite-login")
def kite_login():
    if not API_KEY:
        return "API Key not configured.", 500
    return redirect(f"https://kite.zerodha.com/connect/login?v=3&api_key={API_KEY}")

@app.route("/callback")
def callback():
    request_token = request.args.get("request_token")
    if not request_token:
        return "Request token not found", 400
    data = {
        "api_key": API_KEY,
        "request_token": request_token,
        "secret": API_SECRET
    }
    response = requests.post("https://api.kite.trade/session/token", data=data)
    if response.status_code == 200:
        session["access_token"] = response.json()["data"]["access_token"]
        return redirect("/")
    return "Failed to retrieve access token", 500

if __name__ == "__main__":
    app.run(debug=True)
