from __future__ import annotations

import argparse
import getpass
import os

from minioj.accounts import create_administrator
from minioj.config import settings
from minioj.database import engine, init_db


def create_admin(username: str, email: str, password: str) -> None:
    try:
        create_administrator(username, email, password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"Created system administrator {username.strip()!r} (role=system).")
    print(f"Database: {engine.url.render_as_string(hide_password=True)}")
    print(f"Sign in at {settings.root_path}/login; do not register this account again.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="minioj")
    subparsers = parser.add_subparsers(dest="command", required=True)
    admin_parser = subparsers.add_parser("create-admin", help="Create an administrator")
    admin_parser.add_argument("--username", default=os.getenv("MINIOJ_ADMIN_USERNAME"))
    admin_parser.add_argument("--email", default=os.getenv("MINIOJ_ADMIN_EMAIL"))
    admin_parser.add_argument("--password", default=os.getenv("MINIOJ_ADMIN_PASSWORD"))
    subparsers.add_parser("init-db", help="Create database tables and data directories")
    args = parser.parse_args()
    if args.command == "init-db":
        init_db()
        print("MiniOJ database initialized.")
        return
    username = args.username or input("Username: ").strip()
    email = args.email or input("Email: ").strip()
    password = args.password or getpass.getpass("Password: ")
    create_admin(username, email, password)


if __name__ == "__main__":
    main()
