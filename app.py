
from flask import Flask, render_template, request, redirect, url_for
import os
from datetime import datetime

app = Flask(__name__)

TOKEN_FILE = "access_token.txt"

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/token", methods=["GET", "POST"])
def token():
    if request.method == "POST":
        token = request.form.get("token")
        if token:
            with open(TOKEN_FILE, "w") as f:
                f.write(token)
            return render_template("success.html", token=token)
    return render_template("token.html")

@app.route("/options")
def options():
    # Placeholder: Replace with actual stock option logic
    mock_data = [
        {"symbol": "RELIANCE", "price": 2820, "change_pct": 1.2, "stars": [True, False, True, True, False]},
        {"symbol": "TCS", "price": 3880, "change_pct": -0.4, "stars": [False, False, True, False, False]},
        {"symbol": "HDFCBANK", "price": 1630, "change_pct": 0.9, "stars": [True, True, True, True, True]}
    ]
    return render_template("options.html", data=mock_data)

if __name__ == "__main__":
    app.run(debug=True)
