(() => {
  const config = window.MINIOJ;
  const code = document.querySelector("#code-editor");
  const basePath = config.basePath || "";
  const input = document.querySelector("#custom-input");
  const result = document.querySelector("#run-result");
  const runSampleButton = document.querySelector("#run-sample-button");
  const customTestButton = document.querySelector("#custom-test-button");
  const sampleSelect = document.querySelector("#sample-select");
  const submitButton = document.querySelector("#submit-button");
  const workspaceStatus = document.querySelector("#workspace-status");
  const actionButtons = [runSampleButton, customTestButton, submitButton].filter(Boolean);

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
      const retry = response.headers.get("Retry-After");
      const suffix = retry ? ` Try again in ${retry} second(s).` : "";
      throw new Error((detail || `Request failed (${response.status})`) + suffix);
    }
    return payload;
  }

  function normalized(text) {
    return text.replace(/\r\n/g, "\n").split("\n").map((line) => line.trimEnd()).join("\n").trimEnd();
  }

  async function run(stdin, expected = null) {
    actionButtons.forEach((button) => { button.disabled = true; });
    workspaceStatus.textContent = "Running";
    show("Compiling and running…", "pending");
    try {
      const payload = await request(`${basePath}/api/v1/runs`, {source_code: code.value, language: "cpp20", stdin});
      const memory = payload.memory_kb == null ? "unknown" : `${payload.memory_kb} KB`;
      const sections = [`Status: ${payload.status}`, `Exit code: ${payload.exit_code}`, `Time: ${payload.time_ms} ms`, `Memory: ${memory}`];
      let successful = payload.status === "OK";
      if (expected !== null) {
        const matches = normalized(payload.stdout) === normalized(expected);
        successful = successful && matches;
        sections.push(`Sample: ${matches ? "output matches" : "output differs"}`);
        sections.push(`\nexpected\n${expected}`);
      }
      if (payload.stdout) sections.push(`\nstdout\n${payload.stdout}`);
      if (payload.stderr) sections.push(`\nstderr\n${payload.stderr}`);
      if (payload.output_truncated || payload.stdout_truncated || payload.stderr_truncated) sections.push("\nOutput was truncated at the configured byte limit.");
      show(sections.join("\n"), successful ? "success" : "error");
      workspaceStatus.textContent = "Ready";
    } catch (error) {
      show(error.message, "error");
      workspaceStatus.textContent = "Error";
    } finally {
      actionButtons.forEach((button) => { button.disabled = false; });
    }
  }

  if (runSampleButton) {
    runSampleButton.addEventListener("click", () => {
      const sample = config.samples[Number(sampleSelect.value)];
      run(sample.input, sample.output);
    });
  }

  customTestButton.addEventListener("click", () => run(input.value));

  submitButton.addEventListener("click", async () => {
    actionButtons.forEach((button) => { button.disabled = true; });
    workspaceStatus.textContent = "Submitting";
    show("Queueing submission…", "pending");
    try {
      const payload = await request(`${basePath}/api/v1/submissions`, {problem_id: config.problemId, language: "cpp20", source_code: code.value});
      window.location.assign(`${basePath}/submissions/${payload.submission_id}`);
    } catch (error) {
      show(error.message, "error");
      actionButtons.forEach((button) => { button.disabled = false; });
      workspaceStatus.textContent = "Error";
    }
  });
})();
