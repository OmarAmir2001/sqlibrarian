"""Login and the identity behind every request (JWT).

The token carries who the user is (student_id or ta_id); the runner turns that into
row-level security settings. The model never sees it.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from psycopg_pool import AsyncConnectionPool

from sqlibrarian.helpers.config import get_settings
from sqlibrarian.helpers.enums import Role

ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Identity:
    user_id: int
    role: Role
    student_id: int | None = None
    ta_id: int | None = None


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:  # not a bcrypt hash
        return False


def create_token(identity: Identity) -> str:
    s = get_settings()
    claims = {
        "sub": str(identity.user_id),
        "role": identity.role,
        "student_id": identity.student_id,
        "ta_id": identity.ta_id,
        "exp": datetime.now(UTC) + timedelta(minutes=s.jwt_ttl_minutes),
    }
    return jwt.encode(claims, s.require("jwt_secret"), algorithm=ALGORITHM)


def decode_token(token: str) -> Identity:
    claims = jwt.decode(token, get_settings().require("jwt_secret"), algorithms=[ALGORITHM])
    return Identity(
        user_id=int(claims["sub"]),
        role=Role(claims["role"]),
        student_id=claims.get("student_id"),
        ta_id=claims.get("ta_id"),
    )


async def authenticate(pool: AsyncConnectionPool, username: str, password: str) -> Identity:
    """The user, or 401. The same error for an unknown user and a wrong password."""
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT user_id, password_hash, role, student_id, ta_id FROM users WHERE username = %s",
            (username,),
        )
        row = await cur.fetchone()
    if row is None or not verify_password(password, row[1]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong username or password")
    return Identity(user_id=row[0], role=Role(row[2]), student_id=row[3], ta_id=row[4])


def current_identity(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Identity:
    """FastAPI dependency: the logged-in user, or 401."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "log in first")
    try:
        return decode_token(credentials.credentials)
    except jwt.PyJWTError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token") from e


CurrentUser = Annotated[Identity, Depends(current_identity)]
