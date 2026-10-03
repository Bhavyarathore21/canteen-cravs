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

    items = []
    for row in rows:
        item = dict(row)
        item["can_order"] = bool(item["is_available"]) and item["stock"] > 0
        items.append(item)
    return items

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


class MenuItemCreate(BaseModel):
    name: str = Field(min_length=2)
    price: float = Field(gt=0)
    category: str | None = None
    stock: int = Field(default=0, ge=0)


class MenuItemUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2)
    price: float | None = Field(default=None, gt=0)
    category: str | None = None


class StockUpdate(BaseModel):
    stock: int = Field(ge=0)
    reason: str = "restock"


class AvailabilityUpdate(BaseModel):
    is_available: bool


def log_stock(conn, item_id, quantity_after, reason):
    conn.execute(
        "INSERT INTO stock_log (menu_item_id, quantity_after, reason) VALUES (?, ?, ?)",
        (item_id, quantity_after, reason),
    )


def get_item_or_404(conn, item_id):
    item = conn.execute(
        "SELECT * FROM menu_items WHERE id = ?", (item_id,)
    ).fetchone()
    if item is None:
        conn.close()
        raise HTTPException(status_code=404, detail="Menu item not found")
    return item


@app.post("/admin/menu", status_code=201)
def add_menu_item(data: MenuItemCreate, admin: dict = Depends(require_admin)):
    conn = get_connection()
    cur = conn.execute(
        "INSERT INTO menu_items (name, price, category, stock) VALUES (?, ?, ?, ?)",
        (data.name, data.price, data.category, data.stock),
    )
    new_id = cur.lastrowid
    log_stock(conn, new_id, data.stock, "item created")
    conn.commit()
    conn.close()
    return {"message": "Item added", "id": new_id}


@app.put("/admin/menu/{item_id}")
def update_menu_item(
    item_id: int, data: MenuItemUpdate, admin: dict = Depends(require_admin)
):
    conn = get_connection()
    item = get_item_or_404(conn, item_id)
    conn.execute(
        "UPDATE menu_items SET name = ?, price = ?, category = ? WHERE id = ?",
        (
            data.name or item["name"],
            data.price or item["price"],
            data.category or item["category"],
            item_id,
        ),
    )
    conn.commit()
    conn.close()
    return {"message": "Item updated"}


@app.patch("/admin/menu/{item_id}/stock")
def update_stock(
    item_id: int, data: StockUpdate, admin: dict = Depends(require_admin)
):
    conn = get_connection()
    get_item_or_404(conn, item_id)
    conn.execute("UPDATE menu_items SET stock = ? WHERE id = ?", (data.stock, item_id))
    log_stock(conn, item_id, data.stock, data.reason)
    conn.commit()
    conn.close()
    return {"message": "Stock updated", "stock": data.stock}


@app.patch("/admin/menu/{item_id}/availability")
def update_availability(
    item_id: int, data: AvailabilityUpdate, admin: dict = Depends(require_admin)
):
    conn = get_connection()
    item = get_item_or_404(conn, item_id)
    conn.execute(
        "UPDATE menu_items SET is_available = ? WHERE id = ?",
        (int(data.is_available), item_id),
    )
    reason = "marked available" if data.is_available else "marked sold out"
    log_stock(conn, item_id, item["stock"], reason)
    conn.commit()
    conn.close()
    return {"message": reason}