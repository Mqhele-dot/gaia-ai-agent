from flask import Flask, jsonify, render_template

app = Flask(__name__)

@app.route("/")
def index():
    return "Gaia Agent is Live!"

if __name__ == "__main__":
import os

port = int(os.environ.get("PORT", 5000))  # fallback to 5000 if not set
app.run(host="0.0.0.0", port=port)
