(() => {
  const config = window.MINIOJ;
  const code = document.querySelector("#code-editor");
  const basePath = config.basePath || "";
  const input = document.querySelector("#custom-input");
  const result = document.querySelector("#run-result");
  const runButton = document.querySelector("#run-button");
  const submitButton = document.querySelector("#submit-button");

  function show(message, kind = "") {
    result.className = `result-box ${kind}`;
    result.textContent = message;
  }

  async function request(path, body) {
    const response = await fetch(path, {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-CSRF-Token": config.csrfToken},
      body: JSON.stringify(body),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = typeof payload.detail === "object" ? payload.detail.summary : payload.detail;
      throw new Error(detail || `Request failed (${response.status})`);
    }
    return payload;
  }

  runButton.addEventListener("click", async () => {
    runButton.disabled = true;
    show("Compiling and running…", "pending");
    try {
      const payload = await request(`${basePath}/api/v1/runs`, {code: code.value, language: "cpp20", stdin: input.value});
      const sections = [`Status: ${payload.status}`, `Exit code: ${payload.exit_code}`, `Time: ${payload.time_ms} ms`];
      if (payload.stdout) sections.push(`\nstdout\n${payload.stdout}`);
      if (payload.stderr) sections.push(`\nstderr\n${payload.stderr}`);
      show(sections.join("\n"), payload.status === "OK" ? "success" : "error");
    } catch (error) {
      show(error.message, "error");
    } finally {
      runButton.disabled = false;
    }
  });

  submitButton.addEventListener("click", async () => {
    submitButton.disabled = true;
    show("Queueing submission…", "pending");
    try {
      const payload = await request(`${basePath}/api/v1/submissions`, {problem_id: config.problemId, language: "cpp20", source_code: code.value});
      window.location.assign(`${basePath}/submissions/${payload.submission_id}`);
    } catch (error) {
      show(error.message, "error");
      submitButton.disabled = false;
    }
  });
})();
