"""Direct password and signed-token rules with controlled token timestamps."""

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.auth import AUDIENCE, ISSUER, TokenService, hash_password, verify_password
from app.store import Problem


SECRET = "unit-test-secret-that-is-at-least-32-bytes"
NOW = datetime.now(timezone.utc)


def test_password_hashes_are_salted_and_verify_only_correct_password():
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first != second
    assert verify_password("correct horse battery staple", first)
    assert verify_password("correct horse battery staple", second)
    assert not verify_password("wrong horse battery staple", first)
    assert not verify_password("anything", "broken")


def signed(**changes):
    claims = {"sub": "owner-123", "iss": ISSUER, "aud": AUDIENCE,
              "iat": NOW - timedelta(minutes=1), "exp": NOW + timedelta(hours=1)}
    claims.update(changes)
    return jwt.encode(claims, SECRET, algorithm="HS256")


def test_valid_signed_subject():
    assert TokenService(SECRET).verify(signed()) == "owner-123"


@pytest.mark.parametrize("token", [
    "not-a-jwt",
    jwt.encode({"sub": "owner-123", "iss": ISSUER, "aud": AUDIENCE,
                "iat": NOW, "exp": NOW + timedelta(hours=1)},
               "different-secret-that-is-also-long-enough", algorithm="HS256"),
    signed(exp=NOW - timedelta(seconds=1)),
    signed(iss="wrong-issuer"),
    signed(aud="wrong-audience"),
    signed(sub=""),
    jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "iat": NOW,
                "exp": NOW + timedelta(hours=1)}, SECRET, algorithm="HS256"),
])
def test_invalid_token_is_rejected_without_exposing_claims(token):
    with pytest.raises(Problem) as error:
        TokenService(SECRET).verify(token)
    assert (error.value.status, error.value.code) == (401, "UNAUTHORIZED")
    assert "owner-123" not in error.value.message
