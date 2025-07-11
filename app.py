
from flask import Flask, request, render_template_string

app = Flask(__name__)
token = ""

@app.route("/", methods=["GET", "POST"])
def index():
    global token
    message = ""
    if request.method == "POST":
        token = request.form.get("token", "")
        message = "Token updated successfully!"
    return render_template_string("""
        <h1>PriceTheorem Token Manager</h1>
        <form method="post">
            <label>Enter API Token:</label>
            <input type="text" name="token" value="{{token}}" required>
            <input type="submit" value="Update">
        </form>
        <p>{{message}}</p>
    """, token=token, message=message)

if __name__ == "__main__":
    app.run(debug=True)
