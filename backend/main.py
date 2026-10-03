from fastapi import FastAPI
from database import get_connection, create_tables, seed_menu

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