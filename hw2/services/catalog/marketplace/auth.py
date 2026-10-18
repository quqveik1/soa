import base64
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import jwt
from psycopg.errors import UniqueViolation

from marketplace.config import get_settings
from marketplace.database import connect
from marketplace.errors import ApiError


PASSWORD_ITERATIONS = 600_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_ITERATIONS)
    return "$".join(("pbkdf2_sha256", str(PASSWORD_ITERATIONS), base64.b64encode(salt).decode(), base64.b64encode(digest).decode()))


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, digest = encoded.split("$")
        if algorithm != "pbkdf2_sha256" or int(iterations) != PASSWORD_ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt, validate=True), int(iterations))
        return hmac.compare_digest(actual, base64.b64decode(digest, validate=True))
    except (ValueError, TypeError):
        return False


def register(data: dict) -> dict:
    role = data.get("role") or "USER"
    if role not in ("USER", "SELLER"):
        raise ApiError(400, "VALIDATION_ERROR", "Разрешены роли USER и SELLER", {"fields": [{"field": "role", "message": "Недопустимая роль регистрации"}]})
    try:
        with connect() as connection:
            return connection.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, %s) RETURNING id, username, role, created_at",
                (data["username"], hash_password(data["password"]), role),
            ).fetchone()
    except UniqueViolation as error:
        if error.diag.constraint_name != "users_username_unique":
            raise
        raise ApiError(409, "USER_ALREADY_EXISTS", "Пользователь с таким именем уже существует") from error


def _issue_tokens(connection, user: dict) -> dict:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    refresh_id = uuid4()
    refresh_expires = now + timedelta(days=settings.refresh_days)
    shared = {"sub": str(user["id"]), "role": user["role"], "iat": now}
    access = jwt.encode(
        {**shared, "type": "access", "jti": str(uuid4()), "exp": now + timedelta(minutes=settings.access_minutes)},
        settings.jwt_secret,
        algorithm="HS256",
    )
    refresh_token = jwt.encode(
        {**shared, "type": "refresh", "jti": str(refresh_id), "exp": refresh_expires},
        settings.jwt_secret,
        algorithm="HS256",
    )
    connection.execute(
        "INSERT INTO refresh_tokens (id, user_id, expires_at) VALUES (%s, %s, %s)",
        (refresh_id, user["id"], refresh_expires),
    )
    return {"access_token": access, "refresh_token": refresh_token, "token_type": "Bearer", "expires_in": settings.access_minutes * 60}


def login(data: dict) -> dict:
    with connect() as connection:
        user = connection.execute("SELECT id, username, password_hash, role FROM users WHERE username = %s", (data["username"],)).fetchone()
        if user is None:
            hashlib.pbkdf2_hmac("sha256", data["password"].encode(), b"missing-user-salt", PASSWORD_ITERATIONS)
            raise ApiError(401, "CREDENTIALS_INVALID", "Неверное имя пользователя или пароль")
        if not verify_password(data["password"], user["password_hash"]):
            raise ApiError(401, "CREDENTIALS_INVALID", "Неверное имя пользователя или пароль")
        return _issue_tokens(connection, user)


def _decode_token(token: str, expected_type: str) -> dict:
    refresh_token = expected_type == "refresh"
    invalid_code = "REFRESH_TOKEN_INVALID" if refresh_token else "TOKEN_INVALID"
    try:
        claims = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=["HS256"],
            options={"require": ["exp", "iat", "sub", "role", "type", "jti"]},
        )
        if claims["type"] != expected_type or claims["role"] not in ("USER", "SELLER", "ADMIN"):
            raise ValueError("Unexpected token claims")
        claims["user_id"] = UUID(claims["sub"])
        claims["token_id"] = UUID(claims["jti"])
        return claims
    except jwt.ExpiredSignatureError as error:
        code = invalid_code if refresh_token else "TOKEN_EXPIRED"
        raise ApiError(401, code, "Срок действия токена истёк") from error
    except (jwt.InvalidTokenError, ValueError, TypeError, AttributeError, OverflowError) as error:
        raise ApiError(401, invalid_code, "Недействительный токен") from error


def authenticate(access_token: str) -> dict:
    claims = _decode_token(access_token, "access")
    with connect() as connection:
        user = connection.execute("SELECT id, role FROM users WHERE id = %s", (claims["user_id"],)).fetchone()
    if user is None or user["role"] != claims["role"]:
        raise ApiError(401, "TOKEN_INVALID", "Пользователь или роль токена больше не действуют")
    return {"user_id": user["id"], "role": user["role"]}


def refresh(data: dict) -> dict:
    claims = _decode_token(data["refresh_token"], "refresh")
    with connect() as connection:
        stored = connection.execute("SELECT id, user_id, expires_at, revoked_at FROM refresh_tokens WHERE id = %s FOR UPDATE", (claims["token_id"],)).fetchone()
        if stored is None or stored["user_id"] != claims["user_id"] or stored["revoked_at"] is not None or stored["expires_at"] <= datetime.now(timezone.utc):
            raise ApiError(401, "REFRESH_TOKEN_INVALID", "Недействительный refresh token")
        user = connection.execute("SELECT id, role FROM users WHERE id = %s", (stored["user_id"],)).fetchone()
        if user is None:
            raise ApiError(401, "REFRESH_TOKEN_INVALID", "Пользователь больше не существует")
        connection.execute("UPDATE refresh_tokens SET revoked_at = CURRENT_TIMESTAMP WHERE id = %s", (stored["id"],))
        return _issue_tokens(connection, user)
