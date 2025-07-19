def analyze_capsule(text):
    return {
        "length": len(text),
        "complexity": "Low" if len(text) < 100 else "High"
    }

def export_capsules(capsules):
    from flask import Response
    lines = ["timestamp,tag,text"]
    for cap in capsules:
        lines.append(f"{cap['timestamp']},{cap['tag']},{cap['text']}")
    csv_data = "\n".join(lines)
    return Response(csv_data, mimetype="text/csv",
                    headers={"Content-disposition": "attachment; filename=capsules.csv"})