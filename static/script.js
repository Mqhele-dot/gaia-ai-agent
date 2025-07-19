
function saveCapsule() {
  const content = document.getElementById("capsuleInput").value;
  const tag = document.getElementById("capsuleTag").value;
  fetch("/save_capsule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content, tag })
  })
  .then(res => res.json())
  .then(data => {
    document.getElementById("status").innerText = "Capsule Saved";
    loadCapsules();
  });
}

function loadCapsules() {
  fetch("/load_capsules")
    .then(res => res.json())
    .then(data => {
      const container = document.getElementById("capsuleHistory");
      container.innerHTML = "<h3>Capsule History</h3>" + data.map(c =>
        \`<div>[${c.timestamp}] (${c.tag})</div>\`
      ).join("");
    });
}

function analyzeCapsule() {
  const content = document.getElementById("capsuleInput").value;
  fetch("/analyze_capsule", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ content })
  })
  .then(res => res.json())
  .then(data => {
    document.getElementById("capsuleStats").innerText =
      \`Length: \${data.length}, Complexity: \${data.complexity}\`;
  });
}

function exportCapsules() {
  window.location.href = "/export_capsules";
}
