
from flask import Flask, render_template, request, jsonify
import os, json, time
from datetime import datetime

app = Flask(__name__)
capsules = []

@app.route("/")
def index():
    return render_template("dashboard.html", capsules=capsules)

@app.route("/save_capsule", methods=["POST"])
def save_capsule():
    data = request.json
    capsule = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "content": data.get("content", ""),
        "tag": data.get("tag", ""),
        "length": len(data.get("content", "")),
        "complexity": "High" if len(data.get("content", "")) > 100 else "Low"
    }
    capsules.append(capsule)
    return jsonify({"message": "Capsule saved!", "capsules": capsules})

@app.route("/load_capsules", methods=["GET"])
def load_capsules():
    return jsonify(capsules)

@app.route("/export_capsules", methods=["GET"])
def export_capsules():
    response = app.response_class(
        response=json.dumps(capsules, indent=4),
        mimetype='application/json'
    )
    response.headers["Content-Disposition"] = "attachment; filename=capsules.json"
    return response

@app.route("/analyze_capsule", methods=["POST"])
def analyze_capsule():
    text = request.json.get("content", "")
    length = len(text)
    complexity = "High" if length > 100 else "Low"
    return jsonify({"length": length, "complexity": complexity})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
