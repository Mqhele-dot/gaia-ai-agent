
from flask import Flask, render_template, request, jsonify, send_file
import os, json
from datetime import datetime

app = Flask(__name__)
capsule_history = []

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/save_capsule", methods=["POST"])
def save_capsule():
    content = request.json.get("capsule", "")
    tag = request.json.get("tag", "untagged")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    capsule = {"capsule": content, "tag": tag, "timestamp": timestamp}
    capsule_history.append(capsule)
    return jsonify(success=True, message="Capsule saved.", capsule=capsule)

@app.route("/load_capsules", methods=["GET"])
def load_capsules():
    return jsonify(capsules=capsule_history)

@app.route("/analyze_capsule", methods=["POST"])
def analyze_capsule():
    capsule = request.json.get("capsule", "")
    length = len(capsule)
    complexity = "High" if "import" in capsule else "Low"
    return jsonify(length=length, complexity=complexity)

@app.route("/export_capsules", methods=["GET"])
def export_capsules():
    export_path = "capsules.json"
    with open(export_path, "w") as f:
        json.dump(capsule_history, f)
    return send_file(export_path, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
