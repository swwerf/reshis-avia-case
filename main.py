from fastapi.responses import FileResponse
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import sqlite3
import hashlib
import secrets

app = FastAPI(title="Решись Авиа — Динамическое ценообразование")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- БАЗА ДАННЫХ И АВТОРИЗАЦИЯ -----------------

def init_db():
    """Создаем таблицу пользователей при первом запуске"""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT,
            token TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def hash_password(password: str) -> str:
    """Хэширует пароль для безопасного хранения"""
    return hashlib.sha256(password.encode()).hexdigest()

class UserAuth(BaseModel):
    username: str
    password: str

@app.post("/register")
def register(user: UserAuth):
    """Регистрация нового пользователя"""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    
    # Проверяем, есть ли уже такой логин
    cursor.execute("SELECT * FROM users WHERE username=?", (user.username,))
    if cursor.fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Пользователь с таким именем уже существует")
    
    # Сохраняем логин и хэш пароля
    cursor.execute("INSERT INTO users (username, password_hash, token) VALUES (?, ?, ?)",
                   (user.username, hash_password(user.password), ""))
    conn.commit()
    conn.close()
    return {"message": "Успешная регистрация!"}

@app.post("/login")
def login(user: UserAuth):
    """Вход и выдача токена доступа"""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM users WHERE username=?", (user.username,))
    row = cursor.fetchone()
    
    if not row or row[0] != hash_password(user.password):
        conn.close()
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")
    
    # Генерируем случайный токен
    token = secrets.token_hex(16)
    cursor.execute("UPDATE users SET token=? WHERE username=?", (token, user.username))
    conn.commit()
    conn.close()
    
    return {"token": token, "message": "Успешный вход!"}

def get_current_user(authorization: str = Header(None)):
    """Проверка токена пользователя (защита эндпоинтов)"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Отсутствует токен авторизации")
    
    token = authorization.split(" ")[1]
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT username FROM users WHERE token=?", (token,))
    row = cursor.fetchone()
    conn.close()
    
    if not row:
        raise HTTPException(status_code=401, detail="Неверный или устаревший токен")
    return row[0]


# ----------------- БИЗНЕС-ЛОГИКА (КАЛЬКУЛЯТОР) -----------------

routes_df = pd.read_csv("routes.csv")
forecast_df = pd.read_csv("Евланников_Матвей_Александрович_my_forecast.csv")
forecast_df["date"] = pd.to_datetime(forecast_df["date"]).dt.strftime("%Y-%m-%d")

class PredictRequest(BaseModel):
    route: str
    date: str
    price: float

@app.get("/")
def home():
    """Отдает главную страницу сайта"""
    return FileResponse("index.html")

@app.get("/routes")
def get_routes():
    """Отдает список маршрутов (открытый эндпоинт)"""
    return routes_df[["route", "name"]].to_dict(orient="records")

@app.post("/predict")
def calculate_demand(req: PredictRequest, username: str = Depends(get_current_user)):
    """Считает спрос. Защищен авторизацией (Depends)"""
    
    route_info = routes_df[routes_df["route"] == req.route]
    if route_info.empty:
        raise HTTPException(status_code=404, detail="Маршрут не найден")
    route_info = route_info.iloc[0]

    base_price = route_info["base_price"]
    sensitivity = route_info["price_sensitivity"]

    flight = forecast_df[(forecast_df["route"] == req.route) & (forecast_df["date"] == req.date)]
    if flight.empty:
        raise HTTPException(status_code=404, detail="Рейс на указанную дату не найден")
    flight = flight.iloc[0]

    seats = int(flight["seats"])

    demand_05 = flight["0.5_base_demand"]
    wished_05 = demand_05 - sensitivity * (req.price - base_price)
    bought_05 = min(seats, max(0, wished_05))

    quantiles = ["0.1", "0.2", "0.3", "0.4", "0.5", "0.6", "0.7", "0.8", "0.9"]
    distribution = []

    for q in quantiles:
        base_d = flight[f"{q}_base_demand"]
        wished_q = base_d - sensitivity * (req.price - base_price)
        bought_q = min(seats, max(0, wished_q))
        distribution.append({
            "quantile": float(q),
            "wished": round(wished_q, 1),
            "bought": round(bought_q, 1)
        })

    return {
        "route": req.route,
        "date": req.date,
        "price": req.price,
        "seats": seats,
        "wished": round(wished_05, 1),
        "bought": round(bought_05),
        "free_seats": max(0, seats - round(bought_05)),
        "distribution": distribution,
        "user": username  # Возвращаем имя пользователя просто для проверки
    }