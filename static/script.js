
document.addEventListener("DOMContentLoaded", function () {
    const status = document.getElementById("status");
    const input = document.getElementById("capsule-input");

    document.getElementById("save-capsule").onclick = () => {
        status.innerText = "Saving...";
        fetch("/save_capsule", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ capsule: input.value })
        })
        .then(res => res.json())
        .then(data => { status.innerText = data.message; });
    };

    document.getElementById("load-capsule").onclick = () => {
        status.innerText = "Loading...";
        fetch("/load_capsule")
        .then(res => res.json())
        .then(data => {
            input.value = data.capsule || "";
            status.innerText = "Capsule loaded.";
        });
    };

    document.getElementById("run-agent").onclick = () => {
        status.innerText = "Running Gaia agent...";
        fetch("/run_agent", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ capsule: input.value })
        })
        .then(res => res.json())
        .then(data => {
            status.innerText = data.result;
        });
    };
});
