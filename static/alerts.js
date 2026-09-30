(() => {
  document.querySelectorAll(".alert-warning").forEach((alert) => {
    if (alert.querySelector(".alert-dismiss")) return;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "alert-dismiss";
    button.setAttribute("aria-label", "Dismiss warning");
    button.title = "Dismiss warning";
    button.textContent = "×";
    button.addEventListener("click", () => alert.remove());
    alert.classList.add("alert-dismissible");
    alert.appendChild(button);
  });
})();
