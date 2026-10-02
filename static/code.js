(() => {
  const indent = "    ";

  // Keep plain textareas and normal form submission; only code fields capture Tab.
  document.querySelectorAll("textarea.code-editor").forEach((editor) => {
    let moveFocus = false;
    editor.addEventListener("blur", () => { moveFocus = false; });
    editor.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        moveFocus = true;
        return;
      }
      if (event.key !== "Tab") {
        moveFocus = false;
        return;
      }
      if (moveFocus || event.ctrlKey || event.altKey || event.metaKey) {
        moveFocus = false;
        return;
      }
      event.preventDefault();
      const value = editor.value;
      const start = editor.selectionStart;
      const end = editor.selectionEnd;
      const scrollTop = editor.scrollTop;
      const scrollLeft = editor.scrollLeft;
      let from = start;
      let to = end;
      let replacement = indent;
      let nextStart = start + indent.length;
      let nextEnd = nextStart;

      if (event.shiftKey || start !== end) {
        from = value.slice(0, start).lastIndexOf("\n") + 1;
        const last = end > start && value[end - 1] === "\n" ? end - 1 : end;
        const newline = value.indexOf("\n", last);
        to = newline < 0 ? value.length : newline;
        let offset = from;
        const changes = [];
        replacement = value.slice(from, to).split("\n").map((line) => {
          const removed = event.shiftKey ? (line.match(/^(?: {1,4}|\t)/)?.[0].length || 0) : 0;
          const added = event.shiftKey ? "" : indent;
          changes.push({at: offset, removed, added: added.length});
          offset += line.length + 1;
          return added + line.slice(removed);
        }).join("\n");
        function adjusted(position) {
          let delta = 0;
          for (const change of changes) {
            if (position < change.at) break;
            if (position < change.at + change.removed) return change.at + delta;
            delta += change.added - change.removed;
          }
          return position + delta;
        }
        nextStart = adjusted(start);
        nextEnd = adjusted(end);
      }

      // Native insertText retains the browser's undo stack where supported.
      editor.setSelectionRange(from, to);
      let nativeInsert = false;
      try {
        nativeInsert = document.execCommand("insertText", false, replacement);
      } catch {
        // setRangeText is the fallback for browsers without native editing commands.
      }
      if (!nativeInsert) {
        editor.setRangeText(replacement, from, to, "end");
        editor.dispatchEvent(new Event("input", {bubbles: true}));
      }
      editor.setSelectionRange(nextStart, nextEnd);
      editor.scrollTop = scrollTop;
      editor.scrollLeft = scrollLeft;
    });
  });

  const keywords = new Set((
    "alignas alignof asm auto bool break case catch char char8_t char16_t char32_t class " +
    "co_await co_return co_yield concept const consteval constexpr constinit const_cast continue " +
    "decltype default delete do double dynamic_cast else enum explicit export extern false float " +
    "for friend goto if inline int long mutable namespace new noexcept nullptr operator private " +
    "protected public register reinterpret_cast requires return short signed sizeof static " +
    "static_assert static_cast struct switch template this thread_local throw true try typedef " +
    "typeid typename union unsigned using virtual void volatile wchar_t while and or not xor"
  ).split(" "));
  const tokens = /\/\/[^\n]*|\/\*[\s\S]*?(?:\*\/|$)|^[\t ]*#[^\n]*|"(?:\\[\s\S]|[^"\\])*"|'(?:\\[\s\S]|[^'\\])*'|\b(?:0[xX][\da-fA-F]+|0[bB][01]+|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)[fFuUlL]*\b|\b[A-Za-z_]\w*\b/gm;

  function highlight(code, source) {
    if (code.dataset.language !== "cpp20" || source.length > 262144) return;
    const fragment = document.createDocumentFragment();
    let position = 0;
    for (const match of source.matchAll(tokens)) {
      fragment.append(document.createTextNode(source.slice(position, match.index)));
      const text = match[0];
      let kind = "";
      if (text.startsWith("//") || text.startsWith("/*")) kind = "comment";
      else if (text.trimStart().startsWith("#")) kind = "preprocessor";
      else if (text.startsWith('"') || text.startsWith("'")) kind = "string";
      else if (/^\d/.test(text)) kind = "number";
      else if (keywords.has(text)) kind = "keyword";
      if (kind) {
        const span = document.createElement("span");
        span.className = `syntax-${kind}`;
        span.textContent = text;
        fragment.append(span);
      } else {
        fragment.append(document.createTextNode(text));
      }
      position = match.index + text.length;
    }
    fragment.append(document.createTextNode(source.slice(position)));
    // Never parse source as HTML, including strings resembling markup.
    code.replaceChildren(fragment);
  }

  function fallbackCopy(source) {
    const focused = document.activeElement;
    const field = document.createElement("textarea");
    field.value = source;
    field.readOnly = true;
    field.tabIndex = -1;
    field.setAttribute("aria-hidden", "true");
    field.style.cssText = "position:fixed;left:-9999px;top:0";
    document.body.append(field);
    let copied = false;
    try {
      field.select();
      copied = document.execCommand("copy");
    } catch {
      // Report failure without hiding the selectable original source.
    } finally {
      field.remove();
      focused?.focus({preventScroll: true});
    }
    return copied;
  }

  document.querySelectorAll(".source-preview").forEach((preview) => {
    const code = preview.querySelector(".source-view code");
    const button = preview.querySelector("[data-copy-source]");
    const status = preview.querySelector(".copy-status");
    if (!code || !button || !status) return;
    const source = code.textContent;
    highlight(code, source);
    button.disabled = false;
    button.addEventListener("click", async () => {
      button.disabled = true;
      let copied = false;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(source);
          copied = true;
        }
      } catch {
        // Permission denial and plain HTTP both use the local fallback.
      }
      if (!copied) copied = fallbackCopy(source);
      button.disabled = false;
      button.textContent = copied ? "Copied!" : "Copy code";
      status.textContent = copied ? "Source code copied." : "Could not copy automatically. Select the source and copy it manually.";
    });
  });
})();
