"""Authentication: password hashing, server-side sessions, roles, device API keys.

Roles: viewer (read) < operator (control, acknowledge) < admin (devices, settings, audit).
Standard library only (PBKDF2), so it installs cleanly on Termux.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import timedelta

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import (DEVICE_REQUESTS_PER_MINUTE, PBKDF2_ITERATIONS, SESSION_HOURS)
from app.database import get_db
from app.models import Device, User, UserSession, utcnow

SESSION_COOKIE = "session"
ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def hash_password(password: str, iterations: int | None = None) -> str:
    iterations = iterations or PBKDF2_ITERATIONS
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt, expected = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


_DUMMY_HASH = hash_password("dummy-password-for-timing")


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    ok = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    return user if (user and ok) else None


# ---- login throttling (in memory): 5 failures per 5 minutes per ip+username
_FAILS: dict[str, list[float]] = {}
_DEVICE_REQUESTS: dict[str, list[float]] = {}


def throttled(key: str) -> bool:
    now = time.time()
    recent = [t for t in _FAILS.get(key, []) if now - t < 300]
    _FAILS[key] = recent
    return len(recent) >= 5


def record_fail(key: str) -> None:
    _FAILS.setdefault(key, []).append(time.time())


def clear_fails(key: str) -> None:
    _FAILS.pop(key, None)


# ---- sessions
def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(UserSession(token_hash=_sha(token), user_id=user.id,
                       expires_at=utcnow() + timedelta(hours=SESSION_HOURS)))
    db.commit()
    return token


def destroy_session(db: Session, token: str | None) -> None:
    if not token:
        return
    row = db.scalar(select(UserSession).where(UserSession.token_hash == _sha(token)))
    if row:
        db.delete(row)
        db.commit()


def get_user(request: Request, db: Session) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    row = db.scalar(select(UserSession).where(UserSession.token_hash == _sha(token)))
    if row is None:
        return None
    if row.expires_at < utcnow():
        db.delete(row)
        db.commit()
        return None
    return row.user


def ensure_admin(db: Session) -> None:
    """Create the first admin. Password comes from ADMIN_PASSWORD or is generated and printed once."""
    import os

    if db.scalar(select(User.id).limit(1)) is not None:
        return
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD") or secrets.token_urlsafe(12)
    db.add(User(username=username, password_hash=hash_password(password), role="admin"))
    db.commit()
    if not os.environ.get("ADMIN_PASSWORD"):
        print(f"\n*** First run: admin user '{username}' created with password: {password}\n"
              "*** Save it now; it is not shown again. Add users with: python -m app.cli create-user\n",
              flush=True)


# ---- FastAPI dependencies for the JSON API
def csrf_header_ok(request: Request) -> bool:
    cookie = request.cookies.get("csrf_token")
    header = request.headers.get("x-csrf-token")
    return bool(cookie and header and secrets.compare_digest(cookie.encode(), header.encode()))


def require_role(role: str):
    def dependency(request: Request, db: Session = Depends(get_db)) -> User:
        user = get_user(request, db)
        if user is None:
            raise HTTPException(status_code=401, detail="Authentication required")
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK[role]:
            raise HTTPException(status_code=403, detail="Insufficient role")
        if request.method not in SAFE_METHODS and not csrf_header_ok(request):
            raise HTTPException(status_code=403, detail="CSRF check failed")
        return user

    return dependency


# ---- device API keys
def generate_device_key() -> str:
    return "pk_" + secrets.token_urlsafe(32)


def hash_device_key(key: str) -> str:
    return _sha(key)


def device_auth(request: Request, x_device_key: str | None = Header(None), db: Session = Depends(get_db)) -> Device:
    if not x_device_key:
        raise HTTPException(status_code=401, detail="Missing X-Device-Key")
    key_hash = hash_device_key(x_device_key)
    device = db.scalar(select(Device).where(Device.key_hash == key_hash))
    if device is None or not device.enabled:
        raise HTTPException(status_code=401, detail="Invalid or disabled device key")
    now = time.time()
    bucket = [t for t in _DEVICE_REQUESTS.get(key_hash, []) if now - t < 60]
    if len(bucket) >= DEVICE_REQUESTS_PER_MINUTE:
        _DEVICE_REQUESTS[key_hash] = bucket
        raise HTTPException(
            status_code=429,
            detail="Device request rate limit exceeded",
            headers={"Retry-After": "60"},
        )
    bucket.append(now)
    _DEVICE_REQUESTS[key_hash] = bucket
    return device
