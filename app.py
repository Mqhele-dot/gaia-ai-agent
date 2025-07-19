from flask import Flask, render_template, request, jsonify, send_file
import os, json, datetime
from dashboard.utils import analyze_capsule, export_capsules

app = Flask(__name__)

capsules = []

@app.route('/')
def index():
    return render_template("index.html", capsules=capsules)

@app.route('/save_capsule', methods=['POST'])
def save_capsule():
    data = request.get_json()
    capsule = {
        "text": data['text'],
        "tag": data.get('tag', ''),
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    capsules.append(capsule)
    return jsonify({"message": "Capsule saved", "capsules": capsules})

@app.route('/load_capsules', methods=['GET'])
def load_capsules():
    return jsonify(capsules)

@app.route('/analyze_capsule', methods=['POST'])
def analyze():
    text = request.get_json()['text']
    return jsonify(analyze_capsule(text))

@app.route('/export', methods=['GET'])
def export():
    return export_capsules(capsules)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))