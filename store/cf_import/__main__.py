from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ROOT, load_config
from .pipeline import Importer
from .scanner import parse_problem
from .storage import importer_lock


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-shot, resumable Codeforces -> MiniOJ importer"
    )
    parser.add_argument(
        "--config", type=Path, default=ROOT / "store/cf_import/config.toml"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("scan", help="Scan local paths only; no network or compilation")
    sync = commands.add_parser(
        "sync", help="Scan and cache the complete account AC history"
    )
    sync.add_argument("--refresh-ac", action="store_true")
    imp = commands.add_parser(
        "import", help="Generate/validate tests, upload, then require MiniOJ AC"
    )
    imp.add_argument("--problem", type=parse_problem)
    imp.add_argument("--limit", type=int)
    imp.add_argument(
        "--dry-run",
        action="store_true",
        help="Stop after generating and validating tests; never contact MiniOJ",
    )
    imp.add_argument(
        "--force",
        action="store_true",
        help="Regenerate and reverify using the same remote problem id",
    )
    imp.add_argument("--refresh-ac", action="store_true")
    imp.add_argument(
        "--retry-statements",
        action="store_true",
        help="One new attempt for previously failed statement fetches",
    )
    args = parser.parse_args()
    if getattr(args, "limit", None) is not None and args.limit < 1:
        parser.error("--limit must be positive")
    try:
        cfg = load_config(args.config)
        with importer_lock(cfg.state_file.with_suffix(".lock")):
            importer = Importer(cfg)
            if args.command == "scan":
                problems, records = importer.discover()
                print(
                    f"Total local solutions: {len(records)}\nLocal problems: {len(problems)}\n"
                    f"Unresolved: {sum(r['status'] == 'unresolved' for r in records)}\n"
                    f"Ignored helpers: {sum(r['status'] == 'ignored_helper' for r in records)}"
                )
                return 0
            if args.command == "sync":
                importer.sync(refresh=args.refresh_ac)
                return 0
            summary = importer.run(
                problem=args.problem,
                limit=args.limit,
                dry_run=args.dry_run,
                force=args.force,
                refresh_ac=args.refresh_ac,
                retry_statements=args.retry_statements,
            )
            for name, value in summary.items():
                print(name.replace("_", " ").capitalize() + ": " + str(value))
            return 1 if summary["failed"] or summary["minioj_validation_failed"] else 0
    except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 -- CLI failure boundary
        if isinstance(exc, KeyboardInterrupt):
            print("Interrupted; artifacts/state preserved for resume", file=sys.stderr)
            return 130
        print(f"Importer stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
