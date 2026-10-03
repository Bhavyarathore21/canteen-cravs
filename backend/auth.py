import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from database import get_connection

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY is missing. Add it to backend/.env")

ALGORITHM = "HS256"
TOKEN_HOURS = 12

bearer_scheme = HTTPBearer()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def create_token(user_id: int, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=TOKEN_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """Reads the token and returns the logged-in user."""
    try:
        payload = jwt.decode(
            credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM]
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    conn = get_connection()
    user = conn.execute(
        "SELECT id, name, email, role FROM users WHERE id = ?",
        (int(payload["sub"]),),
    ).fetchone()
    conn.close()

    if user is None:
        raise HTTPException(status_code=401, detail="User not found")
    return dict(user)


def require_admin(user: dict = Depends(get_current_user)):
    """Use this on any endpoint that only admins may call."""
    if user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admins only")
    return user