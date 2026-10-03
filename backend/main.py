from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def home():
    return {"message": "Canteen Cravs API is running"}

@app.get("/menu")
def menu():
    return [
        {"id": 1, "name": "Samosa", "price": 15},
        {"id": 2, "name": "Chai", "price": 10},
        {"id": 3, "name": "Veg Sandwich", "price": 40},
    ]