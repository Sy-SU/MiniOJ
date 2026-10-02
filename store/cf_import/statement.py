from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin

from .config import Config
from .contracts import Statement
from .http import HTTPClient, RequestPacer
from .scanner import ProblemId
from .storage import now, read_json, write_json, write_text


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def find(self, cls: str) -> list[Node]:
        found = [self] if cls in self.attrs.get("class", "").split() else []
        for child in self.children:
            if isinstance(child, Node):
                found.extend(child.find(cls))
        return found

    def plain(self) -> str:
        return "".join(c.plain() if isinstance(c, Node) else c for c in self.children)


class TreeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"br", "img", "hr", "meta", "link", "input", "wbr", "source"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def markdown(node: Node, url: str) -> str:
    if node.tag in {"script", "style"}:
        return ""
    value = "".join(
        markdown(c, url) if isinstance(c, Node) else c for c in node.children
    )
    value = value.replace("$$$", "$")
    if node.tag == "br":
        return "\n"
    if node.tag in {"p", "div", "pre", "ul", "ol"}:
        return value.strip() + "\n\n"
    if node.tag == "li":
        return "- " + value.strip() + "\n"
    if node.tag in {"b", "strong"}:
        return "**" + value + "**"
    if node.tag in {"i", "em"}:
        return "*" + value + "*"
    if node.tag == "sup":
        return "^{" + value + "}"
    if node.tag == "sub":
        return "_{" + value + "}"
    if node.tag == "a":
        return f"[{value}]({urljoin(url, node.attrs.get('href', ''))})"
    if node.tag == "img":
        return (
            f"![{node.attrs.get('alt', '')}]({urljoin(url, node.attrs.get('src', ''))})"
        )
    return value


def sample_text(node: Node) -> str:
    lines = node.find("test-example-line")
    if lines:
        return "\n".join(n.plain() for n in lines) + "\n"

    def visit(n):
        if isinstance(n, str):
            return n
        return "\n" if n.tag == "br" else "".join(visit(c) for c in n.children)

    return visit(node).strip("\r\n") + "\n"


def section_markdown(node: Node, url: str) -> str:
    # MiniOJ supplies the section heading. Keep only Codeforces' section body,
    # including ordinary paragraphs that happen to begin with "Note".
    body = Node(
        node.tag,
        node.attrs,
        [
            c
            for c in node.children
            if not isinstance(c, Node)
            or "section-title" not in c.attrs.get("class", "").split()
        ],
    )
    return markdown(body, url).strip()


def parse_statement(html: str, pid: ProblemId, metadata: dict) -> Statement:
    parser = TreeParser()
    parser.feed(html)
    statements = parser.root.find("problem-statement")
    if len(statements) != 1:
        raise ValueError(
            "Expected one Codeforces problem-statement (possibly blocked/challenge page)"
        )
    root = statements[0]

    def one(cls: str) -> Node:
        nodes = root.find(cls)
        if len(nodes) != 1:
            raise ValueError(f"Missing/ambiguous statement section: {cls}")
        return nodes[0]

    titles = one("header").find("title")
    if len(titles) != 1:
        raise ValueError("Missing/ambiguous problem header title")
    title = titles[0].plain().strip()
    expected = f"{pid.index}. {metadata['name']}"
    if " ".join(title.split()) != " ".join(expected.split()):
        raise ValueError("Statement title/index disagrees with official metadata")
    time_match = re.search(r"([0-9.]+)\s*seconds?", one("time-limit").plain())
    memory_match = re.search(r"([0-9]+)\s*megabytes?", one("memory-limit").plain())
    if not time_match or not memory_match:
        raise ValueError("Cannot parse official time/memory limits")
    excluded = {
        "header",
        "input-specification",
        "output-specification",
        "sample-tests",
        "note",
    }
    body = "".join(
        markdown(c, pid.url) if isinstance(c, Node) else c
        for c in root.children
        if not isinstance(c, Node)
        or not (set(c.attrs.get("class", "").split()) & excluded)
    ).strip()

    def section(cls):
        return section_markdown(one(cls), pid.url)

    input_format, output_format = (
        section("input-specification"),
        section("output-specification"),
    )
    samples = []
    sample_root = one("sample-tests")
    inputs, outputs = sample_root.find("input"), sample_root.find("output")
    if len(inputs) != len(outputs):
        raise ValueError("Unpaired Codeforces samples")
    for a, b in zip(inputs, outputs):

        def pre(n):
            nodes = [c for c in n.children if isinstance(c, Node) and c.tag == "pre"]
            if len(nodes) != 1:
                raise ValueError("Sample missing pre block")
            return sample_text(nodes[0])

        samples.append({"input": pre(a), "output": pre(b)})
    notes = root.find("note")
    return Statement(
        problem_id=pid.key,
        title=metadata["name"],
        statement=body,
        input_format=input_format,
        output_format=output_format,
        constraints=input_format + "\n\n" + body,
        notes=section_markdown(notes[0], pid.url) if notes else "",
        samples=samples,
        source_url=pid.url,
        time_limit_ms=round(float(time_match[1]) * 1000),
        memory_limit_mb=int(memory_match[1]),
    )


class StatementProvider:
    def __init__(self, cfg: Config, http: HTTPClient | None = None):
        self.cfg = cfg
        self.http = http or HTTPClient(max_bytes=4 * 1024 * 1024)
        self.pacer = RequestPacer(cfg.cache_dir / "cf-last-request.json")

    def get(
        self, pid: ProblemId, metadata: dict, *, retry_failed: bool = False
    ) -> Statement:
        directory = self.cfg.generated_dir / str(pid.contest_id) / pid.index
        cache = directory / "statement.json"
        failure = directory / "statement_fetch_error.json"
        if cache.exists():
            value = Statement.model_validate(read_json(cache))
            if (
                value.problem_id != pid.key
                or value.source_url != pid.url
                or value.title != metadata["name"]
            ):
                raise ValueError("Cached statement belongs to a different problem")
            html_path = directory / "statement.html"
            if html_path.exists():
                parser = TreeParser()
                parser.feed(html_path.read_text(encoding="utf-8"))
                roots = parser.root.find("problem-statement")
                notes = roots[0].find("note") if len(roots) == 1 else []
                if (
                    len(notes) == 1
                    and value.notes == markdown(notes[0], pid.url).strip()
                ):
                    cleaned = section_markdown(notes[0], pid.url)
                    if cleaned != value.notes:
                        value.notes = cleaned
                        write_json(cache, value.model_dump())
        else:
            if failure.exists() and not retry_failed:
                raise ValueError(
                    "Statement fetch previously failed; supply local statement.json or use --retry-statements"
                )
            try:
                self.pacer.wait(
                    max(
                        self.cfg.codeforces.interval_seconds,
                        self.cfg.codeforces.statement_interval_seconds,
                    )
                )
                html = self.http.request("GET", pid.url, text=True)
                write_text(directory / "statement.html", html)
                value = parse_statement(html, pid, metadata)
                write_json(cache, value.model_dump())
                failure.unlink(missing_ok=True)
            except Exception as exc:
                write_json(failure, {"error": str(exc), "updated_at": now()})
                raise
        write_text(directory / "statement.md", value.statement)
        return value
