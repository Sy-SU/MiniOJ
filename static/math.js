(() => {
  function renderMath(root = document) {
    if (!window.katex || !root) return;

    const formulas = [];
    if (root.matches?.(".math.inline, .math.block")) formulas.push(root);
    formulas.push(...root.querySelectorAll(".math.inline, .math.block"));

    for (const formula of formulas) {
      if (formula.dataset.mathRendered === "true") continue;
      const source = formula.textContent;
      window.katex.render(source, formula, {
        displayMode: formula.classList.contains("block"),
        throwOnError: false,
        strict: "warn",
        trust: false,
        maxExpand: 1000,
        maxSize: 10,
      });
      formula.dataset.mathRendered = "true";
    }
  }

  window.renderMiniOJMath = renderMath;
  renderMath();
})();
