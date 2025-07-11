
from flask import Flask, request, render_template_string
from kiteconnect import KiteConnect

app = Flask(__name__)

api_key = "3denqx6d967kltkc"
api_secret = "de1r8i93e8txcn1qaaeivkhovepbpbra"
kite = KiteConnect(api_key=api_key)

HTML_FORM = """
<!DOCTYPE html>
<html>
<head>
    <title>Get Access Token</title>
</head>
<body style="font-family: Arial; text-align: center; margin-top: 100px;">
    <h2>Zerodha Access Token Generator</h2>
    <form method="post">
        <input type="text" name="request_token" placeholder="Paste your request_token here" size="50" required><br><br>
        <input type="submit" value="Get Access Token">
    </form>
    {% if token %}
        <h3>Your Access Token:</h3>
        <p style="font-size: 18px; color: green;">{{ token }}</p>
    {% elif error %}
        <p style="color: red;">{{ error }}</p>
    {% endif %}
</body>
</html>
"""

@app.route("/")
def home():
    return '''
    <h2>Welcome to Price Theorem</h2>
    <p><a href="/get-token" style="font-size: 18px; color: blue;">🔐 Get Zerodha Access Token</a></p>
    '''

@app.route("/get-token", methods=["GET", "POST"])
def get_token():
    token = None
    error = None
    if request.method == "POST":
        request_token = request.form.get("request_token")
        try:
            data = kite.generate_session(request_token, api_secret=api_secret)
            token = data["access_token"]
        except Exception as e:
            error = str(e)
    return render_template_string(HTML_FORM, token=token, error=error)

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=10000)
