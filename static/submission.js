(() => {
  document.querySelectorAll(".testcase-preview-toggle").forEach((button) => {
    button.addEventListener("click", () => {
      const preview = document.getElementById(button.getAttribute("aria-controls"));
      preview.hidden = !preview.hidden;
      button.setAttribute("aria-expanded", String(!preview.hidden));
    });
  });

  const config = window.MINIOJ_SUBMISSION;
  if (!config) return;
  const progress = document.querySelector("#submission-progress");
  const verdict = document.querySelector("#submission-verdict");
  let failures = 0;

  async function poll() {
    try {
      const response = await fetch(`${config.basePath || ""}/api/v1/submissions/${config.submissionId}`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message || `Status request failed (${response.status})`);
      failures = 0;
      verdict.textContent = payload.verdict || payload.status;
      verdict.className = `verdict verdict-large verdict-${payload.verdict || payload.status}`;
      if (payload.status === "FINISHED") {
        window.location.reload();
        return;
      }
      progress.textContent = `This submission is ${payload.status.toLowerCase()}. Waiting for the Worker…`;
    } catch (error) {
      failures += 1;
      progress.textContent = `${error.message}. Retrying automatically…`;
    }
    window.setTimeout(poll, Math.min(1000 * (2 ** failures), 10000));
  }

  window.setTimeout(poll, 500);
})();
