
from flask import Flask, render_template, request, jsonify
import os, json
from datetime import datetime

app = Flask(__name__)

CAPSULE_DIR = "data/capsules"
os.makedirs(CAPSULE_DIR, exist_ok=True)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/save_capsule", methods=["POST"])
def save_capsule():
    content = request.json
    capsule_text = content.get("capsule", "")
    tag = content.get("tag", "")
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"{CAPSULE_DIR}/{timestamp}__{tag}.json"
    with open(filename, "w") as f:
        json.dump(content, f)
    return jsonify({"message": "Capsule saved successfully!"})

@app.route("/load_capsules", methods=["GET"])
def load_capsules():
    capsule_files = os.listdir(CAPSULE_DIR)
    capsules = []
    for fname in capsule_files:
        with open(os.path.join(CAPSULE_DIR, fname)) as f:
            capsules.append(json.load(f))
    return jsonify(capsules)

@app.route("/analyze_capsule", methods=["POST"])
def analyze_capsule():
    capsule = request.json.get("capsule", "")
    length = len(capsule)
    complexity = "High" if len(set(capsule.split())) > 15 else "Low"
    return jsonify({"length": length, "complexity": complexity})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
