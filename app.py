from flask import Flask, request, redirect, render_template_string
import os

app = Flask(__name__)

kite_api_key = os.getenv("KITE_API_KEY")
kite_api_secret = os.getenv("KITE_SECRET")

@app.route("/")
def home():
    kite_login_url = f"https://kite.trade/connect/login?api_key={kite_api_key}" if kite_api_key else "#"
    return render_template_string("""
        <h2>Welcome to Price Theorem Token Manager</h2>
        {% if kite_api_key %}
            <p><a href='{{ kite_login_url }}' target='_blank'>🔗 Connect to Kite & Generate Token</a></p>
        {% else %}
            <p style='color:red;'>⚠️ Kite API Key not set in environment.</p>
        {% endif %}
    """, kite_login_url=kite_login_url, kite_api_key=kite_api_key)

@app.route("/token", methods=["GET", "POST"])
def save_token():
    if request.method == "POST":
        token = request.form.get("token")
        with open("access_token.txt", "w") as f:
            f.write(token)
        return "✅ Access token saved!"
    return render_template_string("""
        <form method="post">
            <label>Enter Kite Access Token:</label><br>
            <input name="token" required>
            <button type="submit">Save</button>
        </form>
    """)
