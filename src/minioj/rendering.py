from __future__ import annotations

from markdown_it import MarkdownIt
from markupsafe import Markup
from mdit_py_plugins.dollarmath import dollarmath_plugin

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


def render_markdown(value: object) -> Markup:
    """Render Markdown while escaping embedded HTML and unsafe URLs."""
    return Markup(_markdown.render(str(value or "")))
