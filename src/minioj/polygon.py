from __future__ import annotations

import hashlib
import io
import re
import stat
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from minioj.config import settings
from minioj.judge.testlib import (
    BUNDLE_LIMIT_BYTES,
    FILE_LIMIT_BYTES,
    MAX_FILES,
    SCORING_CHECKERS,
    STANDARD_CHECKERS,
    VENDOR_DIR,
    CheckerBundle,
    safe_bundle_path,
)
from minioj.models import Problem, utcnow
from minioj.problems import _problem_directory, add_testcase_batch
from minioj.security import PROBLEM_ID_RE

ASSET_NAME_RE = re.compile(r"^[0-9a-f]{64}\.(?:png|jpg|gif|webp)$")


@dataclass
class PolygonPackage:
    values: dict
    cases: list[tuple[bytes, bytes]]
    case_types: list[str]
    assets: dict[str, bytes]
    language: str


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list[_Node | str] = field(default_factory=list)


class _HTML(HTMLParser):
    def __init__(self, text: str):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self.stack = [self.root]
        self.feed(text)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"img", "br", "hr", "meta", "link", "input"}:
            if len(self.stack) >= 128:
                raise ValueError("Polygon statement HTML is nested too deeply.")
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _section(node: _Node, name: str) -> _Node | None:
    if name in node.attrs.get("class", "").split():
        return node
    for child in node.children:
        if isinstance(child, _Node) and (found := _section(child, name)):
            return found
    return None


class _Archive:
    def __init__(self, data: bytes):
        if len(data) > settings.polygon_archive_limit_bytes:
            raise ValueError("Polygon ZIP exceeds the configured upload limit.")
        self.zip = zipfile.ZipFile(io.BytesIO(data))
        self.entries = {}
        total = 0
        infos = self.zip.infolist()
        if len(infos) > 10000:
            raise ValueError("Polygon ZIP contains too many entries (maximum 10000).")
        for info in infos:
            name = info.filename.replace("\\", "/")
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or ":" in name
                or "\x00" in name
                or str(path) != name.rstrip("/")
                or stat.S_ISLNK(info.external_attr >> 16)
                or info.flag_bits & 1
            ):
                raise ValueError("Polygon ZIP contains an unsafe or encrypted entry.")
            if info.is_dir():
                continue
            if name in self.entries:
                raise ValueError("Polygon ZIP contains duplicate paths.")
            self.entries[name] = info
            total += info.file_size
        if total > settings.polygon_expanded_limit_bytes:
            raise ValueError("Expanded Polygon ZIP exceeds the configured size limit.")
        descriptors = [
            name for name in self.entries if PurePosixPath(name).name == "problem.xml"
        ]
        if len(descriptors) != 1:
            raise ValueError(
                "Select a single-problem Polygon ZIP with one problem.xml."
            )
        self.root = str(PurePosixPath(descriptors[0]).parent)
        self.root = "" if self.root == "." else self.root + "/"

    def read(self, path: str, limit: int) -> bytes:
        path = path.replace("\\", "/")
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts or ":" in path:
            raise ValueError("Polygon descriptor contains an unsafe path.")
        info = self.entries.get(self.root + str(relative))
        if info is None:
            raise ValueError(f"Polygon ZIP is missing a required file: {path}")
        if info.file_size > limit:
            raise ValueError(f"Polygon file exceeds its size limit: {path}")
        with self.zip.open(info) as stream:
            value = stream.read(limit + 1)
        if len(value) > limit:
            raise ValueError(f"Polygon file exceeds its size limit: {path}")
        return value

    def text(self, path: str, limit: int) -> str:
        try:
            return self.read(path, limit).decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Polygon file must be UTF-8: {path}") from exc


def _checker_bundle(archive: _Archive, root: ET.Element) -> dict:
    node = root.find("assets/checker")
    if node is None:
        raise ValueError("Polygon ZIP must declare its checker.")
    name = node.get("name", "")
    if len(name) > 100:
        raise ValueError("Polygon checker name is too long.")
    if name.removeprefix("std::") in SCORING_CHECKERS:
        raise ValueError(
            "Scoring Polygon checkers are unsupported; MiniOJ uses AC/WA verdicts."
        )
    if node.get("type", "testlib") != "testlib":
        raise ValueError("Only C++ testlib Polygon checkers are supported.")
    source = node.find("source")
    files = {}
    if source is not None:
        if not source.get("type", "").startswith("cpp"):
            raise ValueError(
                "The Polygon checker must include C++ source, not a binary."
            )
        entrypoint = safe_bundle_path(source.get("path", ""))
        files[entrypoint] = archive.text(entrypoint, settings.source_limit_bytes)
        total_size = len(files[entrypoint].encode("utf-8"))
        directory = PurePosixPath(entrypoint).parent
        declared = {f.get("path", "") for f in root.findall("files/resources/file")}
        for path in archive.entries:
            if not path.startswith(archive.root):
                continue
            relative = path[len(archive.root) :]
            resource = PurePosixPath(relative)
            if resource.suffix.lower() not in {".h", ".hpp", ".hh", ".hxx", ".inc"}:
                continue
            if (
                directory in resource.parents
                or relative in declared
                or relative == "testlib.h"
            ):
                safe_bundle_path(relative)
                if len(files) >= MAX_FILES:
                    raise ValueError("Checker bundle contains too many files.")
                if total_size + archive.entries[path].file_size > BUNDLE_LIMIT_BYTES:
                    raise ValueError("Checker source bundle exceeds 4 MiB.")
                files[relative] = archive.text(relative, FILE_LIMIT_BYTES)
                total_size += len(files[relative].encode("utf-8"))
    else:
        standard = name.removeprefix("std::")
        if not name.startswith("std::") or standard not in STANDARD_CHECKERS:
            raise ValueError(
                "Unsupported Polygon checker: export its C++ testlib source in the ZIP."
            )
        entrypoint = "checker.cpp"
        files[entrypoint] = (VENDOR_DIR / "checkers" / standard).read_text(
            encoding="utf-8"
        )
    local_header = str(PurePosixPath(entrypoint).parent / "testlib.h")
    if local_header not in files and "testlib.h" not in files:
        packaged_headers = [
            value
            for path, value in files.items()
            if PurePosixPath(path).name == "testlib.h"
        ]
        if len(packaged_headers) > 1:
            raise ValueError(
                "Ambiguous packaged testlib.h; place it beside the checker source."
            )
        files[local_header] = (
            packaged_headers[0]
            if packaged_headers
            else (VENDOR_DIR / "testlib.h").read_text(encoding="utf-8")
        )
    encoded = CheckerBundle(entrypoint, files).serialize()
    return {
        "checker": "testlib",
        "checker_name": name or PurePosixPath(entrypoint).name,
        "checker_bundle": encoded,
        "checker_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def _pattern(pattern: str, index: int) -> str:
    match = re.search(r"%(?:0([1-8]))?d", pattern)
    if match is None or pattern.count("%") != 1:
        raise ValueError("Polygon test path must contain one %d or %0Nd placeholder.")
    width = int(match.group(1) or "1")
    return pattern[: match.start()] + str(index).zfill(width) + pattern[match.end() :]


def _image_extension(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "webp"
    raise ValueError(
        "Statement images must be PNG, JPEG, GIF or WebP; SVG is unsupported."
    )


def _markdown(node: _Node | str, archive, directory, problem_id, assets) -> str:
    if isinstance(node, str):
        # Polygon uses $$$ for inline math and $$$$$$ for display math.
        text = node.replace("$$$$$$", "$$").replace("$$$", "$")
        parts = re.split(r"(\$\$[\s\S]*?\$\$|\$[^$]*\$)", text)
        return "".join(
            part if index % 2 else part.replace("[", "\\[").replace("]", "\\]")
            for index, part in enumerate(parts)
        )
    if (
        node.tag in {"script", "style", "head"}
        or "section-title" in node.attrs.get("class", "").split()
    ):
        return ""
    if node.tag == "img":
        source = node.attrs.get("src", "")
        if urlsplit(source).scheme or source.startswith(("/", "//")):
            raise ValueError(
                "Statement images must be included locally in the Polygon ZIP."
            )
        data = archive.read(str(directory / source), 8 * 1024 * 1024)
        filename = hashlib.sha256(data).hexdigest() + "." + _image_extension(data)
        assets[filename] = data
        return f"\n\n![Statement image](/problems/{problem_id}/assets/{filename})\n\n"
    content = "".join(
        _markdown(c, archive, directory, problem_id, assets) for c in node.children
    )
    classes = node.attrs.get("class", "").split()
    if node.tag in {"b", "strong"} or "tex-font-style-bf" in classes:
        return f"**{content}**"
    if node.tag in {"i", "em"} or "tex-font-style-it" in classes:
        return f"*{content}*"
    if node.tag == "code" or "tex-font-style-tt" in classes:
        return f"`{content}`"
    if node.tag == "br":
        return "\n"
    if node.tag == "li":
        return f"\n- {content.strip()}"
    if node.tag == "pre":
        return f"\n\n```\n{content.strip()}\n```\n\n"
    if node.tag in {"p", "div", "center", "ul", "ol", "table", "tr"}:
        return f"\n\n{content.strip()}\n\n"
    return content


def parse_polygon(
    data: bytes, problem_id: str = "", language: str = "auto"
) -> PolygonPackage:
    try:
        archive = _Archive(data)
        with archive.zip:
            xml = archive.read("problem.xml", 1024 * 1024)
            xml = xml.decode("utf-8-sig")
            if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper():
                raise ValueError("Polygon XML entities and DTDs are unsupported.")
            root = ET.fromstring(xml)
            if root.tag != "problem":
                raise ValueError("Not a Polygon problem descriptor.")
            problem_id = problem_id.strip() or root.get("short-name", "")
            if not PROBLEM_ID_RE.fullmatch(problem_id):
                raise ValueError(
                    "Problem ID must use 3–80 letters, numbers or hyphens."
                )
            judging = root.find("judging")
            if (
                judging is None
                or judging.get("input-file")
                or judging.get("output-file")
            ):
                raise ValueError(
                    "Only standard-input/standard-output Polygon problems are supported."
                )
            if root.find("assets/interactor") is not None:
                raise ValueError("Interactive Polygon problems are unsupported.")
            testsets = judging.findall("testset")
            testset = next((t for t in testsets if t.get("name") == "tests"), None)
            if testset is None:
                if len(testsets) != 1:
                    raise ValueError("Polygon ZIP must contain one primary testset.")
                testset = testsets[0]
            tests = testset.findall("tests/test")
            count = int(testset.findtext("test-count", "0"))
            if not 1 <= count <= 1000 or len(tests) != count:
                raise ValueError("Polygon test count is inconsistent or exceeds 1000.")
            time_ms = int(testset.findtext("time-limit", "0"))
            memory_bytes = int(testset.findtext("memory-limit", "0"))
            memory_mb = (memory_bytes + 1024 * 1024 - 1) // (1024 * 1024)
            if not 100 <= time_ms <= 30000 or not 16 <= memory_mb <= 2048:
                raise ValueError("Polygon limits are outside MiniOJ's supported range.")
            checker_values = _checker_bundle(archive, root)
            statements = [
                s
                for s in root.findall("statements/statement")
                if s.get("type") == "text/html"
            ]
            languages = {s.get("language") for s in statements}
            if language == "auto":
                language = next(
                    (lang for lang in ("chinese", "english") if lang in languages), ""
                )
                if not language and statements:
                    language = statements[0].get("language", "")
            statement = next(
                (s for s in statements if s.get("language") == language), None
            )
            if statement is None:
                raise ValueError(
                    "Selected language has no HTML statement; export a full Polygon package with HTML."
                )
            path = statement.get("path", "")
            dom = _HTML(archive.text(path, 2 * 1024 * 1024)).root
            assets = {}
            fields = {}
            for field_name, class_name in (
                ("statement", "legend"),
                ("input_specification", "input-specification"),
                ("output_specification", "output-specification"),
                ("notes", "note"),
            ):
                section = _section(dom, class_name)
                text = (
                    _markdown(
                        section, archive, PurePosixPath(path).parent, problem_id, assets
                    )
                    if section
                    else ""
                )
                fields[field_name] = re.sub(r"\n{3,}", "\n\n", text).strip()
            if not fields["statement"]:
                raise ValueError("Polygon HTML statement has no problem legend.")
            title = next(
                (
                    n.get("value", "")
                    for n in root.findall("names/name")
                    if n.get("language") == language
                ),
                "",
            )
            if not title or len(title) > 255:
                raise ValueError("Polygon problem title is missing or too long.")
            cases, case_types = [], []
            for index, test in enumerate(tests, 1):
                pair = tuple(
                    archive.read(
                        _pattern(testset.findtext(tag, ""), index),
                        settings.testcase_file_limit_bytes,
                    )
                    for tag in ("input-path-pattern", "answer-path-pattern")
                )
                for value in pair:
                    value.decode("utf-8")
                cases.append(pair)
                case_types.append(
                    "sample"
                    if test.get("sample") == "true"
                    else "generated"
                    if test.get("method") == "generated"
                    else "hidden"
                )
            standard_source = None
            main = root.find("assets/solutions/solution[@tag='main']/source")
            if main is not None:
                if not main.get("type", "").startswith("cpp"):
                    raise ValueError("The main Polygon solution must be C++ source.")
                standard_source = archive.text(
                    main.get("path", ""), settings.source_limit_bytes
                )
            source_url = root.get("url") or None
            if source_url and (
                urlsplit(source_url).scheme not in {"http", "https"}
                or len(source_url) > 1000
            ):
                raise ValueError("Invalid Polygon source URL.")
            values = dict(
                id=problem_id,
                title=title,
                **fields,
                time_limit_ms=time_ms,
                memory_limit_mb=memory_mb,
                **checker_values,
                source="Polygon",
                source_id=root.get("short-name", "")[:100],
                source_url=source_url,
                standard_source=standard_source,
                standard_sha256=hashlib.sha256(standard_source.encode()).hexdigest()
                if standard_source
                else None,
                standard_updated_at=utcnow() if standard_source else None,
            )
            return PolygonPackage(values, cases, case_types, assets, language)
    except (
        zipfile.BadZipFile,
        ET.ParseError,
        UnicodeDecodeError,
        RuntimeError,
        NotImplementedError,
    ) as exc:
        raise ValueError("Invalid Polygon ZIP, XML or UTF-8 content.") from exc


def import_polygon(db: Session, package: PolygonPackage, admin_id: int) -> Problem:
    problem_id = package.values["id"]
    if db.get(Problem, problem_id) is not None:
        raise ValueError(
            "Problem ID already exists; choose a new ID. Existing problems are not overwritten."
        )
    asset_directory = _problem_directory(problem_id) / "assets"
    if asset_directory.is_symlink():
        raise ValueError("Unsafe problem asset directory.")
    created = []
    try:
        problem = Problem(**package.values, created_by=admin_id)
        db.add(problem)
        db.flush()
        if package.assets:
            asset_directory.mkdir(parents=True, exist_ok=True)
        for name, payload in package.assets.items():
            path = asset_directory / name
            with path.open("xb") as stream:
                created.append(path)
                stream.write(payload)
        add_testcase_batch(
            db, problem, "hidden", package.cases, case_types=package.case_types
        )
        return problem
    except BaseException as exc:
        db.rollback()
        for path in created:
            path.unlink(missing_ok=True)
        for directory in (
            asset_directory,
            _problem_directory(problem_id) / "tests",
            _problem_directory(problem_id),
        ):
            try:
                directory.rmdir()
            except OSError:
                pass
        if isinstance(exc, IntegrityError):
            raise ValueError("Problem ID already exists; choose a new ID.") from exc  # noqa: TRY004
        raise


def problem_asset_path(problem_id: str, name: str) -> Path:
    if not ASSET_NAME_RE.fullmatch(name):
        raise ValueError("Invalid statement image name.")
    directory = _problem_directory(problem_id) / "assets"
    path = directory / name
    if directory.is_symlink() or path.is_symlink() or not path.is_file():
        raise ValueError("Statement image not found.")
    return path
