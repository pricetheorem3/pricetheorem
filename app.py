
from flask import Flask, render_template, jsonify
import os

app = Flask(__name__)

@app.route("/")
def home():
    api_key = os.environ.get("KITE_API_KEY", None)
    if not api_key:
        return "⚠️ Kite API Key not set in environment."
    return render_template("index.html", kite_api_key=api_key)
