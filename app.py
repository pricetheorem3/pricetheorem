
from flask import Flask, request, render_template, redirect
import os

app = Flask(__name__)

TOKEN_FILE = "token.txt"
ADMIN_PASSWORD = "priceadmin"  # You can change this

@app.route("/")
def home():
    return 