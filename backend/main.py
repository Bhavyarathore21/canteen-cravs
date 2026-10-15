from typing import Literal
import sqlite3

from fastapi import Depends, FastAPI, HTTPException, Query
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



class OrderItemIn(BaseModel):
    menu_item_id: int
    quantity: int = Field(ge=1, le=20)


class OrderCreate(BaseModel):
    items: list[OrderItemIn] = Field(min_length=1)
    payment_method: Literal["UPI", "Cash"]


@app.post("/orders", status_code=201)
def place_order(data: OrderCreate, user: dict = Depends(get_current_user)):
    # If the same item appears twice, add the quantities together
    wanted = {}
    for line in data.items:
        wanted[line.menu_item_id] = wanted.get(line.menu_item_id, 0) + line.quantity

    conn = get_connection()
    try:
        # Block cash orders after 3 or more no-shows
        if data.payment_method == "Cash":
            row = conn.execute(
                "SELECT no_show_count FROM users WHERE id = ?", (user["id"],)
            ).fetchone()
            if row["no_show_count"] >= 3:
                raise HTTPException(
                    status_code=403,
                    detail="Cash orders are disabled after repeated no-shows. Please pay by UPI.",
                )

        total = 0
        lines = []  # (item_id, name, quantity, price, stock_after)

        for item_id, qty in wanted.items():
            item = conn.execute(
                "SELECT id, name, price FROM menu_items WHERE id = ?", (item_id,)
            ).fetchone()
            if item is None:
                raise HTTPException(status_code=404, detail=f"Menu item {item_id} not found")

            # Check stock and reduce it in ONE step
            cur = conn.execute(
                "UPDATE menu_items SET stock = stock - ? "
                "WHERE id = ? AND is_available = 1 AND stock >= ?",
                (qty, item_id, qty),
            )
            if cur.rowcount == 0:
                raise HTTPException(
                    status_code=409,
                    detail=f"Sorry, {item['name']} is sold out or has less than {qty} left",
                )

            stock_after = conn.execute(
                "SELECT stock FROM menu_items WHERE id = ?", (item_id,)
            ).fetchone()["stock"]
            lines.append((item_id, item["name"], qty, item["price"], stock_after))
            total += qty * item["price"]

        # Pickup code: A001, A002 ... counting today's orders
        count_today = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE date(created_at) = date('now')"
        ).fetchone()[0]
        order_code = f"A{count_today + 1:03d}"

        cur = conn.execute(
            "INSERT INTO orders (order_code, user_id, total, status, payment_method, payment_status) "
            "VALUES (?, ?, ?, 'Placed', ?, 'Pending')",
            (order_code, user["id"], total, data.payment_method),
        )
        order_id = cur.lastrowid

        for item_id, name, qty, price, stock_after in lines:
            conn.execute(
                "INSERT INTO order_items (order_id, menu_item_id, quantity, price) "
                "VALUES (?, ?, ?, ?)",
                (order_id, item_id, qty, price),
            )
            log_stock(conn, item_id, stock_after, f"order {order_code}")

        conn.execute(
            "INSERT INTO order_status_log (order_id, status) VALUES (?, 'Placed')",
            (order_id,),
        )
        conn.commit()  # everything is saved together

    except Exception:
        conn.rollback()  # something failed, so undo everything
        raise
    finally:
        conn.close()

    return {
        "order_id": order_id,
        "order_code": order_code,
        "total": total,
        "status": "Placed",
        "payment_method": data.payment_method,
        "payment_status": "Pending",
        "items": [
            {"name": name, "quantity": qty, "price": price}
            for _, name, qty, price, _ in lines
        ],
    }


@app.get("/orders/my")
def my_orders(user: dict = Depends(get_current_user)):
    conn = get_connection()
    orders = conn.execute(
        "SELECT id, order_code, total, status, payment_method, payment_status, created_at "
        "FROM orders WHERE user_id = ? ORDER BY id DESC",
        (user["id"],),
    ).fetchall()

    result = []
    for order in orders:
        items = conn.execute(
            "SELECT m.name, oi.quantity, oi.price FROM order_items oi "
            "JOIN menu_items m ON m.id = oi.menu_item_id WHERE oi.order_id = ?",
            (order["id"],),
        ).fetchall()
        result.append({**dict(order), "items": [dict(i) for i in items]})

    conn.close()
    return result

VALID_NEXT = {
    "Placed": ["Preparing", "Cancelled"],
    "Preparing": ["Ready", "Cancelled"],
    "Ready": ["Completed"],
}


class StatusUpdate(BaseModel):
    status: Literal["Preparing", "Ready", "Completed", "Cancelled"]


def set_order_status(conn, order_id, new_status):
    conn.execute("UPDATE orders SET status = ? WHERE id = ?", (new_status, order_id))
    conn.execute(
        "INSERT INTO order_status_log (order_id, status) VALUES (?, ?)",
        (order_id, new_status),
    )


def restore_stock(conn, order_id, order_code, reason):
    items = conn.execute(
        "SELECT menu_item_id, quantity FROM order_items WHERE order_id = ?",
        (order_id,),
    ).fetchall()
    for it in items:
        conn.execute(
            "UPDATE menu_items SET stock = stock + ? WHERE id = ?",
            (it["quantity"], it["menu_item_id"]),
        )
        new_stock = conn.execute(
            "SELECT stock FROM menu_items WHERE id = ?", (it["menu_item_id"],)
        ).fetchone()["stock"]
        log_stock(conn, it["menu_item_id"], new_stock, f"{reason} {order_code}")


def flag_refund_if_paid(conn, order):
    if order["payment_status"] == "Paid":
        conn.execute(
            "UPDATE orders SET payment_status = 'Refund Pending' WHERE id = ?",
            (order["id"],),
        )


@app.get("/admin/orders")
def admin_orders(status: str | None = None, admin: dict = Depends(require_admin)):
    conn = get_connection()
    base = (
        "SELECT o.id, o.order_code, o.total, o.status, o.payment_method, "
        "o.payment_status, o.created_at, u.name AS customer "
        "FROM orders o JOIN users u ON u.id = o.user_id "
    )
    if status:
        orders = conn.execute(base + "WHERE o.status = ? ORDER BY o.id", (status,)).fetchall()
    else:
        orders = conn.execute(
            base + "WHERE o.status IN ('Placed', 'Preparing', 'Ready') ORDER BY o.id"
        ).fetchall()

    result = []
    for order in orders:
        items = conn.execute(
            "SELECT m.name, oi.quantity FROM order_items oi "
            "JOIN menu_items m ON m.id = oi.menu_item_id WHERE oi.order_id = ?",
            (order["id"],),
        ).fetchall()
        result.append({**dict(order), "items": [dict(i) for i in items]})
    conn.close()
    return result


@app.patch("/admin/orders/{order_id}/status")
def update_order_status(
    order_id: int, data: StatusUpdate, admin: dict = Depends(require_admin)
):
    conn = get_connection()
    try:
        order = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        if data.status not in VALID_NEXT.get(order["status"], []):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot move an order from {order['status']} to {data.status}",
            )

        if data.status == "Cancelled":
            if order["status"] == "Placed":  # nothing cooked yet
                restore_stock(conn, order_id, order["order_code"], "cancelled by canteen, order")
            flag_refund_if_paid(conn, order)

        set_order_status(conn, order_id, data.status)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"message": f"Order {order['order_code']} is now {data.status}"}


@app.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, user: dict = Depends(get_current_user)):
    conn = get_connection()
    try:
        order = conn.execute(
            "SELECT * FROM orders WHERE id = ? AND user_id = ?", (order_id, user["id"])
        ).fetchone()
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")

        # Cancel only if still 'Placed' (check and change in one step)
        cur = conn.execute(
            "UPDATE orders SET status = 'Cancelled' WHERE id = ? AND status = 'Placed'",
            (order_id,),
        )
        if cur.rowcount == 0:
            raise HTTPException(
                status_code=400,
                detail=f"Too late to cancel: the order is already {order['status']}",
            )

        conn.execute(
            "INSERT INTO order_status_log (order_id, status) VALUES (?, 'Cancelled')",
            (order_id,),
        )
        restore_stock(conn, order_id, order["order_code"], "cancelled by student, order")
        flag_refund_if_paid(conn, order)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"message": f"Order {order['order_code']} cancelled"}


@app.post("/admin/orders/expire-no-shows")
def expire_no_shows(
    minutes: int = Query(default=30, ge=0), admin: dict = Depends(require_admin)
):
    conn = get_connection()
    try:
        stale = conn.execute(
            """
            SELECT o.id, o.user_id FROM orders o
            WHERE o.status = 'Ready'
              AND (SELECT MAX(changed_at) FROM order_status_log l
                   WHERE l.order_id = o.id AND l.status = 'Ready') <= datetime('now', ?)
            """,
            (f"-{minutes} minutes",),
        ).fetchall()

        for o in stale:
            set_order_status(conn, o["id"], "Expired")
            conn.execute(
                "UPDATE users SET no_show_count = no_show_count + 1 WHERE id = ?",
                (o["user_id"],),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"expired_orders": len(stale), "after_minutes": minutes}