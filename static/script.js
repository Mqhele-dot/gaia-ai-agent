
let capsules = [];

document.getElementById('theme-toggle').onclick = () => {
    const theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', theme);
};

function saveCapsule() {
    const text = document.getElementById('capsule-input').value;
    const tag = document.getElementById('capsule-tag').value;
    const time = new Date().toISOString();
    capsules.push({ text, tag, time });
    renderHistory();
}

function loadCapsules() {
    renderHistory();
}

function analyzeCapsule() {
    const text = document.getElementById('capsule-input').value;
    const stats = `Length: ${text.length}, Complexity: ${text.split(" ").length > 20 ? "High" : "Low"}`;
    document.getElementById('capsule-stats').innerText = stats;
}

function exportCapsules() {
    const data = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(capsules));
    const a = document.createElement('a');
    a.setAttribute("href", data);
    a.setAttribute("download", "capsules.json");
    document.body.appendChild(a);
    a.click();
    a.remove();
}

function renderHistory() {
    const history = document.getElementById('capsule-history');
    history.innerHTML = '<h2>Capsule History</h2>';
    capsules.forEach(c => {
        const div = document.createElement('div');
        div.textContent = `[${c.time}] (${c.tag}): ${c.text.slice(0, 60)}...`;
        history.appendChild(div);
    });
}
