
from flask import Flask, request, render_template_string
import os

app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def home():
    message = ""
    if request.method == 'POST':
        token = request.form.get('token')
        with open('access_token.txt', 'w') as f:
            f.write(token)
        message = "Access token updated successfully!"
    token = ""
    if os.path.exists('access_token.txt'):
        with open('access_token.txt', 'r') as f:
            token = f.read()
    return render_template_string("""
        <!doctype html>
        <title>Price Theorem | Token Manager</title>
        <h1>Update Kite API Access Token</h1>
        <form method=post>
          <input type=text name=token value="{{ token }}" style="width: 300px;">
          <input type=submit value=Update>
        </form>
        <p>{{ message }}</p>
        <hr>
        <h3>Volume Signal (Example Logic)</h3>
        <p>This site is live and integrated with your token setup.</p>
    """, token=token, message=message)

if __name__ == "__main__":
    app.run(debug=True)
