"""
demo_seed.py - fills an EMPTY database with demo accounts and a fuller menu.

Free hosting wipes the database whenever the server restarts, so every fresh start
runs this. It does nothing if the database already has users, so your local data is safe.
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


def seed_demo():
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
