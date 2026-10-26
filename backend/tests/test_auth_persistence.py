"""Issue #5: account persistence and verified local bearer authentication."""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import jwt
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.auth import AUDIENCE, ISSUER
from app.db import UserRecord
from app.main import create_app


SECRET = "test-only-persistent-auth-secret-32-bytes"
PASSWORD = "CorrectHorse123!"


def app_for(path: Path):
    return create_app(secret=SECRET, database_url=f"sqlite:///{path}")


def register(client, email="  Alice@Example.com  "):
    response = client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 201, response.text
    return response.json()


def test_migration_normalization_hashes_and_restart(tmp_path):
    path = tmp_path / "accounts.db"
    assert not path.exists()
    with TestClient(app_for(path)) as client:
        account = register(client)
        UUID(account["user"]["id"])
        assert account["user"]["email"] == "alice@example.com"
        assert set(account) == {"access_token", "token_type", "user"}
        assert client.get("/api/v1/workers", headers={"Authorization": f"Bearer {account['access_token']}"}).json() == {
            "items": [], "total": 0, "limit": 20, "offset": 0,
        }
        assert client.get("/api/v1/runs", headers={"Authorization": f"Bearer {account['access_token']}"}).json()["items"] == []
        duplicate = client.post("/api/v1/auth/register", json={"email": "ALICE@example.com", "password": PASSWORD})
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "ACCOUNT_EXISTS"
        assert client.post("/api/v1/auth/register", json={"email": "invalid", "password": PASSWORD}).status_code == 422
        assert client.post("/api/v1/auth/register", json={"email": "other@example.com", "password": "short"}).status_code == 422

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(users)")}
        assert columns == {"id", "email", "password_hash", "created_at"}
        indexes = list(connection.execute("PRAGMA index_list(users)"))
        assert any(row[1] == "ix_users_email" and row[2] == 1 for row in indexes)
        user = connection.execute("SELECT id, email, password_hash, created_at FROM users").fetchone()
        assert user[0] == account["user"]["id"]
        assert user[1] == "alice@example.com"
        assert user[2].startswith("pbkdf2_sha256$310000$")
        assert PASSWORD not in user[2]
        assert user[3].endswith("Z")
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0002_app_data"

    with TestClient(app_for(path)) as restarted:
        login = restarted.post("/api/v1/auth/login", json={"email": "Alice@Example.com", "password": PASSWORD})
        assert login.status_code == 200
        assert login.json()["user"] == account["user"]
        assert restarted.get("/api/v1/workers", headers={"Authorization": f"Bearer {account['access_token']}"}).status_code == 200
        wrong = restarted.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "WrongHorse123!"})
        unknown = restarted.post("/api/v1/auth/login", json={"email": "missing@example.com", "password": PASSWORD})
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["error"] == unknown.json()["error"] == {
            "code": "INVALID_CREDENTIALS", "message": "Invalid email or password"
        }


def test_signed_token_claims_and_rejection_cases(tmp_path):
    path = tmp_path / "claims.db"
    with TestClient(app_for(path)) as client:
        account = register(client)
        subject = account["user"]["id"]
        token = account["access_token"]
        claims = jwt.decode(token, SECRET, algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER)
        assert claims["sub"] == subject
        assert claims["iss"] == ISSUER
        assert claims["aud"] == AUDIENCE
        assert claims["exp"] - claims["iat"] == 8 * 3600
        assert jwt.get_unverified_header(token)["alg"] == "HS256"

        current = datetime.now(timezone.utc)
        base = {"sub": subject, "iss": ISSUER, "aud": AUDIENCE,
                "iat": current, "exp": current + timedelta(hours=8)}
        variants = [
            None,
            "not.a.jwt",
            jwt.encode(base, "another-test-secret-with-sufficient-length", algorithm="HS256"),
            jwt.encode({**base, "exp": current - timedelta(seconds=1)}, SECRET, algorithm="HS256"),
            jwt.encode({**base, "iss": "wrong-issuer"}, SECRET, algorithm="HS256"),
            jwt.encode({**base, "aud": "wrong-audience"}, SECRET, algorithm="HS256"),
            jwt.encode({key: value for key, value in base.items() if key != "sub"}, SECRET, algorithm="HS256"),
        ]
        for variant in variants:
            headers = {"Authorization": f"Bearer {variant}"} if variant is not None else {}
            response = client.get("/api/v1/workers", headers=headers)
            assert response.status_code == 401, variant
            assert response.json()["error"]["code"] == "UNAUTHORIZED"

        with client.app.state.users.sessions() as session:
            session.execute(delete(UserRecord).where(UserRecord.id == subject))
            session.commit()
        assert client.get("/api/v1/workers", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_short_configured_secret_is_rejected(tmp_path):
    try:
        create_app(secret="too-short", database_url=f"sqlite:///{tmp_path / 'short.db'}")
    except ValueError as error:
        assert "at least 32 bytes" in str(error)
    else:
        raise AssertionError("A short signing secret was accepted")


def test_server_requires_configured_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("RUNGUARD_AUTH_SECRET", raising=False)
    app = create_app(database_url=f"sqlite:///{tmp_path / 'missing-secret.db'}")
    try:
        with TestClient(app):
            pass
    except ValueError as error:
        assert "must be configured" in str(error)
    else:
        raise AssertionError("The API started without a signing secret")
