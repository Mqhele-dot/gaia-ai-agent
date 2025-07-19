from flask import Flask, jsonify
import json

app = Flask(__name__)

@app.route("/api/capsules")
def capsules():
    with open("data/capsules.json", "r") as f:
        return jsonify(json.load(f))

@app.route("/api/logs")
def logs():
    with open("data/gaia_log.jsonl", "r") as f:
        return jsonify([json.loads(line) for line in f])

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)