"""
profile_routes.py - lets a logged-in user (student or admin) edit their own profile.

    PATCH /me            change name and/or email
    POST  /me/password   change password (needs the current password)

The shared demo accounts (...@canteen.demo) are read-only: otherwise one visitor could
change the demo password and lock everyone else out.
"""
import sqlite3

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import get_current_user, hash_password, verify_password
from database import get_connection

router = APIRouter()

DEMO_SUFFIX = "@canteen.demo"
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=60)
    email: str | None = Field(default=None, max_length=120, pattern=EMAIL_PATTERN)


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=72)
    new_password: str = Field(min_length=6, max_length=72)


def _block_demo_accounts(user):
    if user["email"].lower().endswith(DEMO_SUFFIX):
        raise HTTPException(
            status_code=403,
            detail="Demo accounts are shared by all visitors, so they cannot be edited. "
                   "Create your own account to edit a profile.",
        )


@router.patch("/me")
def update_profile(data: ProfileUpdate, user: dict = Depends(get_current_user)):
    _block_demo_accounts(user)
    name = data.name.strip() if data.name else None
    email = data.email.strip().lower() if data.email else None
    if name is not None and len(name) < 2:
        raise HTTPException(status_code=400, detail="Name must have at least 2 characters")
    if name is None and email is None:
        raise HTTPException(status_code=400, detail="Nothing to update")

    conn = get_connection()
    try:
        try:
            conn.execute(
                "UPDATE users SET name = COALESCE(?, name), email = COALESCE(?, email) WHERE id = ?",
                (name, email, user["id"]),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=400, detail="That email is already used by another account")
        row = conn.execute(
            "SELECT id, name, email, role FROM users WHERE id = ?", (user["id"],)
        ).fetchone()
        return dict(row)
    finally:
        conn.close()


@router.post("/me/password")
def change_password(data: PasswordChange, user: dict = Depends(get_current_user)):
    _block_demo_accounts(user)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user["id"],)
        ).fetchone()
        if not verify_password(data.current_password, row["password_hash"]):
            raise HTTPException(status_code=400, detail="Your current password is wrong")
        if data.current_password == data.new_password:
            raise HTTPException(status_code=400, detail="Choose a password different from the current one")
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(data.new_password), user["id"]),
        )
        conn.commit()
        return {"message": "Password changed"}
    finally:
        conn.close()
