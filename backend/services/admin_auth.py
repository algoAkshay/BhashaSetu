"""Small expiring signed-cookie session; no users table or external auth service."""
import hashlib
import hmac
import os
import secrets
import time

from fastapi import HTTPException, Request

COOKIE = "bhasha_admin"
SESSION_SECONDS = 8 * 60 * 60


def settings():
    password = os.environ.get("ADMIN_PASSWORD", "")
    secret = os.environ.get("ADMIN_SESSION_SECRET", "")
    if not password or len(secret) < 32:
        raise HTTPException(503, "Admin access is not configured. Set ADMIN_PASSWORD and a strong ADMIN_SESSION_SECRET (at least 32 characters).")
    secure = os.environ.get("ADMIN_COOKIE_SECURE", "false").lower() == "true"
    # Rotating either environment secret invalidates existing cookies.
    key = hmac.digest(secret.encode(), password.encode(), "sha256")
    return password, key, secure


def create_session(key):
    payload = f"{int(time.time()) + SESSION_SECONDS}.{secrets.token_hex(32)}"
    return payload + "." + hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def require_admin(request: Request):
    _, key, _ = settings()
    token = request.cookies.get(COOKIE, "")
    try:
        expires, nonce, signature = token.split(".")
        payload = f"{expires}.{nonce}"
        expected = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
        valid = secrets.compare_digest(signature, expected) and int(expires) > time.time() and len(nonce) == 64
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise HTTPException(401, "Admin login required.")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not secrets.compare_digest(request.headers.get("X-Admin-CSRF", "").encode(), nonce.encode()):
            raise HTTPException(403, "Invalid admin request. Reload the page and try again.")
    return nonce
