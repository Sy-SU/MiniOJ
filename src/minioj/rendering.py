from __future__ import annotations

from markdown_it import MarkdownIt
from markupsafe import Markup
from mdit_py_plugins.dollarmath import dollarmath_plugin

from minioj.config import settings

_markdown = (
    MarkdownIt(
        "commonmark",
        {
            "breaks": True,
            "html": False,
            "linkify": False,
        },
    )
    .enable("table")
    .use(
        dollarmath_plugin,
        allow_labels=False,
        allow_blank_lines=False,
    )
)


def render_markdown(value: object, root_path: str | None = None) -> Markup:
    """Render Markdown while escaping embedded HTML and unsafe URLs."""
    tokens = _markdown.parse(str(value or ""))
    base_path = settings.root_path if root_path is None else root_path.rstrip("/")
    for token in tokens:
        for child in token.children or []:
            if child.type == "image":
                source = child.attrGet("src") or ""
                if source.startswith("/problems/") and "/assets/" in source:
                    child.attrSet("src", base_path + source)
    return Markup(_markdown.renderer.render(tokens, _markdown.options, {}))
