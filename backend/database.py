import sqlite3

DB_NAME = "canteen.db"


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row          # lets us read rows like dictionaries
    conn.execute("PRAGMA foreign_keys = ON")  # makes the table links enforced
    return conn


def create_tables():
    conn = get_connection()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user', 'admin')),
        no_show_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS menu_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price REAL NOT NULL,
        category TEXT,
        stock INTEGER NOT NULL DEFAULT 0,
        is_available INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_code TEXT NOT NULL,
        user_id INTEGER NOT NULL REFERENCES users(id),
        total REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'Placed',
        payment_method TEXT NOT NULL CHECK (payment_method IN ('UPI', 'Cash')),
        payment_status TEXT NOT NULL DEFAULT 'Pending',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS order_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL REFERENCES orders(id),
        menu_item_id INTEGER NOT NULL REFERENCES menu_items(id),
        quantity INTEGER NOT NULL,
        price REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS order_status_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL REFERENCES orders(id),
        status TEXT NOT NULL,
        changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS stock_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        menu_item_id INTEGER NOT NULL REFERENCES menu_items(id),
        quantity_after INTEGER NOT NULL,
        reason TEXT,
        changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS payment_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL REFERENCES orders(id),
        event TEXT NOT NULL,
        amount REAL,
        reference TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    );
    """)
    conn.commit()
    conn.close()


def seed_menu():
    """Adds a few starter items, only if the menu is empty."""
    conn = get_connection()
    count = conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0]
    if count == 0:
        conn.executemany(
            "INSERT INTO menu_items (name, price, category, stock) VALUES (?, ?, ?, ?)",
            [
                ("Samosa", 15, "Snacks", 50),
                ("Chai", 10, "Beverages", 100),
                ("Veg Sandwich", 40, "Snacks", 30),
            ],
        )
        conn.commit()
    conn.close()