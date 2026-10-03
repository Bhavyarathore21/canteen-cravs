import sqlite3

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from auth import (
    create_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from database import create_tables, get_connection, seed_menu

app = FastAPI()

create_tables()
seed_menu()


@app.get("/")
def home():
    return {"message": "Canteen Cravs API is running"}


@app.get("/menu")
def menu():
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, name, price, category, stock, is_available FROM menu_items"
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2)
    email: str
    password: str = Field(min_length=6, max_length=72)


class LoginRequest(BaseModel):
    email: str
    password: str


@app.post("/register")
def register(data: RegisterRequest):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, 'user')",
            (data.name, data.email.lower(), hash_password(data.password)),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail="Email already registered")
    finally:
        conn.close()
    return {"message": "Account created"}


@app.post("/login")
def login(data: LoginRequest):
    conn = get_connection()
    user = conn.execute(
        "SELECT * FROM users WHERE email = ?", (data.email.lower(),)
    ).fetchone()
    conn.close()

    if user is None or not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Wrong email or password")

    token = create_token(user["id"], user["role"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "role": user["role"],
        "name": user["name"],
    }


@app.get("/me")
def me(user: dict = Depends(get_current_user)):
    return user


@app.get("/admin/ping")
def admin_ping(user: dict = Depends(require_admin)):
    return {"message": f"Welcome admin {user['name']}"}