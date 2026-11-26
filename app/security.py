"""Basic CSRF protection (double-submit cookie) for browser form posts.

A random token is stored in a SameSite=Strict cookie AND embedded in every
form as a hidden field. A POST is accepted only if both values match.
"""
from __future__ import annotations

import secrets

from fastapi import Request, Response
from app.config import COOKIE_SECURE

CSRF_COOKIE = "csrf_token"


def get_or_create_token(request: Request) -> tuple[str, bool]:
    existing = request.cookies.get(CSRF_COOKIE)
    if existing:
        return existing, False
    return secrets.token_urlsafe(32), True


def set_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        CSRF_COOKIE, token, httponly=True, secure=COOKIE_SECURE, samesite="strict", path="/"
    )  # add secure=True when served over HTTPS


def verify_csrf(request: Request, form_token: str | None) -> bool:
    cookie = request.cookies.get(CSRF_COOKIE)
    if not cookie or not form_token:
        return False
    return secrets.compare_digest(cookie.encode(), form_token.encode())
