"""Local demo authentication; tokens are invalidated when the process restarts."""

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .store import Problem


ISSUER = "runguard-demo"
AUDIENCE = "runguard-api"
bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"pbkdf2_sha256$310000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt, digest = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual, bytes.fromhex(digest))
    except (ValueError, TypeError):
        return False


class TokenService:
    def __init__(self, secret: str | None = None):
        self.secret = secret or os.environ.get("RUNGUARD_AUTH_SECRET") or secrets.token_urlsafe(48)
        if len(self.secret.encode()) < 32:
            raise ValueError("RUNGUARD_AUTH_SECRET must contain at least 32 bytes")

    def issue(self, user_id: str) -> str:
        current = datetime.now(timezone.utc)
        return jwt.encode({"sub": user_id, "iss": ISSUER, "aud": AUDIENCE,
                           "iat": current, "exp": current + timedelta(hours=8), "jti": str(uuid4())},
                          self.secret, algorithm="HS256")

    def verify(self, token: str) -> str:
        try:
            payload = jwt.decode(token, self.secret, algorithms=["HS256"],
                                 audience=AUDIENCE, issuer=ISSUER,
                                 options={"require": ["sub", "iss", "aud", "iat", "exp"]})
            subject = payload["sub"]
            if not isinstance(subject, str) or not subject:
                raise ValueError("Missing subject")
            return subject
        except (jwt.PyJWTError, ValueError):
            raise Problem(401, "UNAUTHORIZED", "Invalid or expired bearer token") from None


def current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> str:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise Problem(401, "UNAUTHORIZED", "Bearer token required")
    subject = request.app.state.tokens.verify(credentials.credentials)
    if request.app.state.store.user_by_id(subject) is None:
        raise Problem(401, "UNAUTHORIZED", "Unknown account")
    return subject
