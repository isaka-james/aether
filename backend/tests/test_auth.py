"""Tests for single-user auth: bcrypt credential check and the JWT round-trip used by both
the HTTP dependency (``require_user``) and the WebSocket path (``decode_token``). The known
credentials come from conftest, set before ``auth`` is imported (it hashes the password at
import time).
"""
import time

import jwt
import pytest
from fastapi import HTTPException

from app import auth


def test_correct_credentials_accepted():
    assert auth.verify_credentials("tester", "s3cret-pw") is True


def test_wrong_password_rejected():
    assert auth.verify_credentials("tester", "nope") is False


def test_wrong_username_rejected():
    assert auth.verify_credentials("someone-else", "s3cret-pw") is False


def test_token_round_trip():
    token = auth.create_token("tester")
    assert auth.require_user(token) == "tester"
    assert auth.decode_token(token) == "tester"


def test_missing_token_is_401():
    with pytest.raises(HTTPException) as ei:
        auth.require_user(None)
    assert ei.value.status_code == 401


def test_tampered_token_is_rejected():
    forged = jwt.encode({"sub": "tester", "exp": int(time.time()) + 999},
                        "the-wrong-secret-but-long-enough-to-not-warn", algorithm="HS256")
    with pytest.raises(HTTPException) as ei:
        auth.require_user(forged)
    assert ei.value.status_code == 401
    assert auth.decode_token(forged) is None


def test_expired_token_is_rejected():
    expired = jwt.encode({"sub": "tester", "iat": int(time.time()) - 100,
                          "exp": int(time.time()) - 10},
                         auth._settings.jwt_secret, algorithm="HS256")
    with pytest.raises(HTTPException):
        auth.require_user(expired)
    assert auth.decode_token(expired) is None
