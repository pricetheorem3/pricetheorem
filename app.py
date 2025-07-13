
from flask import Flask, render_template

app = Flask(__name__)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/token")
def token():
    return render_template("token.html")

@app.route("/home")
def homepage():
    return render_template("home.html")

@app.route("/update-token")
def update_token():
    return render_template("update_token.html")

if __name__ == "__main__":
    app.run(debug=True)
