(() => {
  const form = document.querySelector("#problem-form");
  const button = document.querySelector("#markdown-preview-button");
  const panel = document.querySelector("#markdown-preview-panel");
  const content = document.querySelector("#markdown-preview-content");
  const status = document.querySelector("#markdown-preview-status");
  if (!form || !button || !panel || !content || !status) return;

  const basePath = window.MINIOJ_PROBLEM_FORM?.basePath || "";

  button.addEventListener("click", async () => {
    button.disabled = true;
    panel.hidden = false;
    status.textContent = "Rendering…";
    try {
      const response = await fetch(`${basePath}/admin/problems/preview`, {
        method: "POST",
        body: new FormData(form),
      });
      if (!response.ok || response.redirected) {
        throw new Error(`Preview failed (${response.status})`);
      }
      content.innerHTML = await response.text();
      window.renderMiniOJMath?.(content);
      status.textContent = "Up to date";
    } catch (error) {
      content.textContent = error.message;
      status.textContent = "Preview unavailable";
    } finally {
      button.disabled = false;
    }
  });
})();
