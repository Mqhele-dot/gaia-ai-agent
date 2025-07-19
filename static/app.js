
function saveCapsule() {
    const capsule = document.getElementById("capsuleInput").value;
    const tag = document.getElementById("tagInput").value;
    fetch("/save_capsule", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({capsule, tag})
    }).then(res => res.json()).then(data => alert(data.message));
}

function loadHistory() {
    fetch("/load_capsules")
        .then(res => res.json())
        .then(data => {
            const history = document.getElementById("capsuleHistory");
            history.innerHTML = "";
            data.forEach(entry => {
                const li = document.createElement("li");
                li.textContent = `[${entry.tag}] ${entry.capsule.substring(0, 100)}...`;
                history.appendChild(li);
            });
        });
}

function analyzeCapsule() {
    const capsule = document.getElementById("capsuleInput").value;
    fetch("/analyze_capsule", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({capsule})
    }).then(res => res.json()).then(data => {
        alert(`Length: ${data.length}, Complexity: ${data.complexity}`);
    });
}

function exportCapsules() {
    fetch("/load_capsules")
        .then(res => res.json())
        .then(data => {
            const blob = new Blob([JSON.stringify(data, null, 2)], {type: "application/json"});
            const url = URL.createObjectURL(blob);
            const a = document.createElement("a");
            a.href = url;
            a.download = "capsules.json";
            a.click();
        });
}
