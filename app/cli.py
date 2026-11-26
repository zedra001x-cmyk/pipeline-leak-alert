"""User management:  python -m app.cli create-user NAME ROLE   |   reset-password NAME"""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.auth import ROLE_RANK, hash_password
from app.database import SessionLocal, init_db
from app.models import User


def _password() -> str:
    pw = getpass.getpass("Password (min 10 characters): ")
    if len(pw) < 10:
        sys.exit("Password too short.")
    if pw != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    return pw


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create-user")
    create.add_argument("username")
    create.add_argument("role", choices=sorted(ROLE_RANK))
    reset = sub.add_parser("reset-password")
    reset.add_argument("username")
    args = parser.parse_args()

    init_db()
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.username == args.username))
        if args.cmd == "create-user":
            if existing:
                sys.exit("User already exists.")
            db.add(User(username=args.username, password_hash=hash_password(_password()), role=args.role))
            print(f"Created {args.role} '{args.username}'.")
        else:
            if not existing:
                sys.exit("User not found.")
            existing.password_hash = hash_password(_password())
            print("Password updated.")
        db.commit()


if __name__ == "__main__":
    main()
