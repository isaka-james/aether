"""Single-user authentication with bcrypt + JWT bearer tokens."""
import time

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from .config import get_settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login", auto_error=False)

# Hash the configured password once at import time so we never keep plaintext around.
_settings = get_settings()
_PW_HASH = bcrypt.hashpw(_settings.password.encode(), bcrypt.gensalt())


def verify_credentials(username: str, password: str) -> bool:
    if username != _settings.username:
        return False
    return bcrypt.checkpw(password.encode(), _PW_HASH)


def create_token(username: str) -> str:
    now = int(time.time())
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + _settings.jwt_expire_minutes * 60,
    }
    return jwt.encode(payload, _settings.jwt_secret, algorithm="HS256")


def require_user(token: str | None = Depends(oauth2_scheme)) -> str:
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = jwt.decode(token, _settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return payload["sub"]


def decode_token(token: str) -> str | None:
    """Used by the WebSocket path where Depends() isn't available."""
    try:
        return jwt.decode(token, _settings.jwt_secret, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        return None
