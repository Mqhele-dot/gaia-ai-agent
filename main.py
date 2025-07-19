
from flask import Flask, render_template, request, jsonify
import os, json

app = Flask(__name__)

@app.route("/")
def index():
    return render_template("dashboard.html")

@app.route("/api/save", methods=["POST"])
def save_capsule():
    data = request.json
    with open("data/capsules.json", "w") as f:
        json.dump(data, f)
    return jsonify({"message": "Saved successfully"}), 200

@app.route("/api/load", methods=["GET"])
def load_capsule():
    if not os.path.exists("data/capsules.json"):
        return jsonify({"message": "No data found"}), 404
    with open("data/capsules.json", "r") as f:
        data = json.load(f)
    return jsonify(data), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
