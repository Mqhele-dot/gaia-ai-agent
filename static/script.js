async function saveCapsule() {
    const text = document.getElementById("capsuleInput").value;
    const tag = document.getElementById("tagInput").value;
    const res = await fetch("/save_capsule", {
        method: "POST",
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, tag })
    });
    const data = await res.json();
    document.getElementById("status").innerText = "Saved";
    renderCapsules(data.capsules);
}

async function loadCapsules() {
    const res = await fetch("/load_capsules");
    const data = await res.json();
    renderCapsules(data);
}

async function analyzeCapsule() {
    const text = document.getElementById("capsuleInput").value;
    const res = await fetch("/analyze_capsule", {
        method: "POST",
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text })
    });
    const data = await res.json();
    alert("Length: " + data.length + ", Complexity: " + data.complexity);
}

async function exportCapsules() {
    window.location.href = "/export";
}

function renderCapsules(capsules) {
    const container = document.getElementById("capsuleHistory");
    container.innerHTML = "";
    capsules.forEach(c => {
        const div = document.createElement("div");
        div.textContent = `[${c.timestamp}] (${c.tag}) ${c.text}`;
        container.appendChild(div);
    });
}