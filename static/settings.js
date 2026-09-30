(() => {
  const button = document.getElementById("copy-api-token");
  const token = document.getElementById("new-api-token");
  const status = document.getElementById("token-copy-status");
  if (!button || !token || !status) return;

  function selectToken() {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(token);
    selection.removeAllRanges();
    selection.addRange(range);
    token.focus();
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    let copied = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(token.textContent);
        copied = true;
      }
    } catch {
      // Clipboard permission may be denied; try copying the selected text below.
    }
    if (!copied) {
      try {
        selectToken();
        copied = document.execCommand("copy");
      } catch {
        copied = false;
      }
    }
    button.disabled = false;
    button.textContent = copied ? "Copied!" : "Copy";
    status.textContent = copied
      ? "Token copied to clipboard."
      : "Could not copy automatically. Select the token and copy it manually.";
  });
})();
