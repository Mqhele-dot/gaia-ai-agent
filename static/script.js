
document.addEventListener("DOMContentLoaded", function () {
    const status = document.getElementById("status");
    const input = document.getElementById("capsule-input");
    const saveBtn = document.getElementById("save-capsule");
    const loadBtn = document.getElementById("load-capsule");

    saveBtn.addEventListener("click", function () {
        const capsule = input.value;
        status.innerText = "Saving...";
        fetch("/save_capsule", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ capsule })
        }).then(res => res.json())
          .then(data => {
              status.innerText = data.message;
          });
    });

    loadBtn.addEventListener("click", function () {
        status.innerText = "Loading...";
        fetch("/load_capsule")
            .then(res => res.json())
            .then(data => {
                input.value = data.capsule || "";
                status.innerText = "Capsule loaded.";
            });
    });
});
