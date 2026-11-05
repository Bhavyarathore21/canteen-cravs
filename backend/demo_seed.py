"""
demo_seed.py - fills an EMPTY database with demo accounts and a fuller menu, and makes
sure the real admin exists.

Free hosting wipes the database whenever the server restarts, so every fresh start runs
this. The demo part does nothing if the database already has users, so your local data
is safe.

THE REAL ADMIN: set these environment variables (on Render, under Environment):
    ADMIN_EMAIL     the admin's login email
    ADMIN_PASSWORD  a strong password (6 to 72 characters)
    ADMIN_NAME      the name shown in the app (optional)
On every start, the account is created if it does not exist, or promoted to admin if it
does. The password is never stored in the code or in GitHub.
"""
import os

from auth import hash_password
from database import get_connection

# name, price, category, starting stock
EXTRA_MENU = [
    ("Coffee", 20, "Beverages", 40),
    ("Cold Drink", 20, "Beverages", 40),
    ("Cold Coffee", 40, "Beverages", 25),
    ("Vada Pav", 20, "Snacks", 50),
    ("Maggi", 30, "Snacks", 36),
    ("Poha", 25, "Breakfast", 25),
    ("Veg Thali", 70, "Meals", 55),
    ("Chole Bhature", 60, "Meals", 40),
    ("Fried Rice", 55, "Meals", 30),
]


def _seed_demo_data():
    conn = get_connection()
    try:
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
            return  # the database already has data, so leave it alone

        accounts = [
            ("Demo Admin", "admin@canteen.demo", os.getenv("DEMO_ADMIN_PASSWORD", "admin12345"), "admin"),
            ("Demo Student", "student@canteen.demo", os.getenv("DEMO_STUDENT_PASSWORD", "student12345"), "user"),
        ]
        for name, email, password, role in accounts:
            conn.execute(
                "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, ?)",
                (name, email, hash_password(password), role),
            )

        for name, price, category, stock in EXTRA_MENU:
            if not conn.execute("SELECT 1 FROM menu_items WHERE name = ?", (name,)).fetchone():
                cur = conn.execute(
                    "INSERT INTO menu_items (name, price, category, stock) VALUES (?, ?, ?, ?)",
                    (name, price, category, stock),
                )
                conn.execute(
                    "INSERT INTO stock_log (menu_item_id, quantity_after, reason) VALUES (?, ?, 'demo seed')",
                    (cur.lastrowid, stock),
                )
        conn.commit()
    finally:
        conn.close()


def seed_admin_from_env():
    """Creates (or promotes) the real admin described by ADMIN_EMAIL / ADMIN_PASSWORD."""
    email = os.getenv("ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("ADMIN_PASSWORD", "")
    if not email or not password:
        return
    if not 6 <= len(password) <= 72:
        print("ADMIN_PASSWORD must be 6 to 72 characters: the admin account was not created.")
        return
    name = os.getenv("ADMIN_NAME", "").strip() or "Admin"

    conn = get_connection()
    try:
        row = conn.execute("SELECT id, role FROM users WHERE email = ?", (email,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO users (name, email, password_hash, role) VALUES (?, ?, ?, 'admin')",
                (name, email, hash_password(password)),
            )
        elif row["role"] != "admin":
            conn.execute("UPDATE users SET role = 'admin' WHERE id = ?", (row["id"],))
        conn.commit()
    finally:
        conn.close()


def seed_demo():
    """Called once when the server starts."""
    _seed_demo_data()
    seed_admin_from_env()
