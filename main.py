import asyncio
import logging
import os
import sys
import re
import sqlite3
from datetime import datetime, timedelta
import json

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

# Импорты для API ЮKassa и webhook-сервера
from yookassa import Configuration, Payment, Webhook
from aiohttp import web, ClientSession
import asyncio

# ------------------ КОНФИГУРАЦИЯ ------------------
ADMIN_ID = 850409726
CHANNEL_ID = "@MuslimkaNikah"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(ENV_PATH)
BOT_TOKEN = os.getenv("BOT_TOKEN")
YOOKASSA_SHOP_ID = os.getenv("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = os.getenv("YOOKASSA_SECRET_KEY")

if not BOT_TOKEN:
    print("❌ Токен бота не найден в .env")
    sys.exit(1)
if not YOOKASSA_SHOP_ID or not YOOKASSA_SECRET_KEY:
    print("⚠️ YOOKASSA_SHOP_ID или YOOKASSA_SECRET_KEY не найдены. Оплата не будет работать.")

# Настраиваем ЮKassa
if YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY:
    Configuration.account_id = YOOKASSA_SHOP_ID
    Configuration.secret_key = YOOKASSA_SECRET_KEY

# ------------------ БАЗА ДАННЫХ ------------------
def init_db():
    conn = sqlite3.connect("ankets.db")
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            gender TEXT,
            name TEXT,
            age INTEGER,
            height INTEGER,
            weight INTEGER,
            city TEXT,
            marital TEXT,
            intimate TEXT,
            religiosity TEXT,
            about TEXT,
            seeking TEXT,
            public_photo TEXT,
            private_photo TEXT,
            price_category TEXT,
            status TEXT DEFAULT 'pending',
            post_message_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            nationality TEXT,
            islam_since TEXT,
            children TEXT
        )
    """)
    cur.execute("PRAGMA table_info(users)")
    columns = [col[1] for col in cur.fetchall()]
    if "post_message_id" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN post_message_id INTEGER")
    if "nationality" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN nationality TEXT")
    if "islam_since" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN islam_since TEXT")
    if "children" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN children TEXT")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_user_id INTEGER,
            to_user_id INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(from_user_id, to_user_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS paid_contacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            buyer_id INTEGER,
            seller_id INTEGER,
            chat_id INTEGER,
            expires_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Таблица для хранения временных платежей (чтобы связать оплату с услугой)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS pending_payments (
            payment_id TEXT PRIMARY KEY,
            user_id INTEGER,
            service_type TEXT,
            service_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

init_db()

# ------------------ ФУНКЦИИ РАБОТЫ С БАЗОЙ ------------------
def save_anketa(data: dict, user_id: int):
    conn = sqlite3.connect("ankets.db")
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO users (
            user_id, gender, name, age, height, weight, city, marital, intimate,
            religiosity, about, seeking, public_photo, private_photo, price_category, status,
            nationality, islam_since, children
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        data.get('gender'),
        data.get('name'),
        data.get('age'),
        data.get('height'),
        data.get('weight'),
        data.get('city'),
        data.get('marital'),
        data.get('intimate'),
        data.get('religiosity'),
        data.get('about'),
        data.get('seeking'),
        data.get('public_photo'),
        data.get('private_photo'),
        data.get('price_category', '700'),
        'pending',
        data.get('nationality'),
        data.get('islam_since'),
        data.get('children')
    ))
    conn.commit()
    conn.close()

def update_status(user_id: int, status: str):
    conn = sqlite3.connect("ankets.db")
    cur = conn.cursor()
    cur.execute("""
        UPDATE users 
        SET status = ? 
        WHERE id = (
            SELECT id FROM users 
            WHERE user_id = ? AND status = 'pending' 
            ORDER BY created_at DESC LIMIT 1
        )
    """, (status, user_id))
    conn.commit()
    conn.close()

def get_anketa(user_id: int):
    conn = sqlite3.connect("ankets.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row

def get_user_by_id(user_id: int):
    conn = sqlite3.connect("ankets.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row

def update_post_message_id(user_id: int, post_id: int):
    conn = sqlite3.connect("ankets.db")
    cur = conn.cursor()
    cur.execute("""
        UPDATE users 
        SET post_message_id = ? 
        WHERE id = (
            SELECT id FROM users 
            WHERE user_id = ? AND status = 'approved' 
            ORDER BY created_at DESC LIMIT 1
        )
    """, (post_id, user_id))
    conn.commit()
    conn.close()

def save_like(from_user: int, to_user: int):
    conn = sqlite3.connect("ankets.db")
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO likes (from_user_id, to_user_id, status) VALUES (?, ?, 'pending')", (from_user, to_user))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def update_like_status(from_user: int, to_user: int, status: str):
    conn = sqlite3.connect("ankets.db")
    cur = conn.cursor()
    cur.execute("UPDATE likes SET status = ? WHERE from_user_id = ? AND to_user_id = ?", (status, from_user, to_user))
    conn.commit()
    conn.close()

def get_like(from_user: int, to_user: int):
    conn = sqlite3.connect("ankets.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM likes WHERE from_user_id = ? AND to_user_id = ?", (from_user, to_user))
    row = cur.fetchone()
    conn.close()
    return row

def get_any_like_between(user1: int, user2: int):
    conn = sqlite3.connect("ankets.db")
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM likes 
        WHERE (from_user_id = ? AND to_user_id = ? OR from_user_id = ? AND to_user_id = ?)
        AND status = 'approved'
    """, (user1, user2, user2, user1))
    row = cur.fetchone()
    conn.close()
    return row

def save_paid_contact(buyer_id: int, seller_id: int, chat_id: int, expires_at: datetime):
    conn = sqlite3.connect("ankets.db")
    cur = conn.cursor()
    cur.execute("INSERT INTO paid_contacts (buyer_id, seller_id, chat_id, expires_at) VALUES (?, ?, ?, ?)",
                (buyer_id, seller_id, chat_id, expires_at))
    conn.commit()
    conn.close()

def save_pending_payment(payment_id: str, user_id: int, service_type: str, service_data: str):
    conn = sqlite3.connect("ankets.db")
    cur = conn.cursor()
    cur.execute("INSERT OR REPLACE INTO pending_payments (payment_id, user_id, service_type, service_data) VALUES (?, ?, ?, ?)",
                (payment_id, user_id, service_type, service_data))
    conn.commit()
    conn.close()

def get_pending_payment(payment_id: str):
    conn = sqlite3.connect("ankets.db")
    cur = conn.cursor()
    cur.execute("SELECT * FROM pending_payments WHERE payment_id = ?", (payment_id,))
    row = cur.fetchone()
    conn.close()
    return row

def delete_pending_payment(payment_id: str):
    conn = sqlite3.connect("ankets.db")
    cur = conn.cursor()
    cur.execute("DELETE FROM pending_payments WHERE payment_id = ?", (payment_id,))
    conn.commit()
    conn.close()

# ------------------ НАСТРОЙКА БОТА ------------------
logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ------------------ КЛАВИАТУРЫ ------------------
main_menu_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Заполнить анкету")],
        [KeyboardButton(text="❓ Помощь")],
        [KeyboardButton(text="🚫 Отменить")]
    ],
    resize_keyboard=True
)

cancel_only_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🚫 Отменить")]
    ],
    resize_keyboard=True
)

nav_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="◀️ Назад")],
        [KeyboardButton(text="🚫 Отменить")]
    ],
    resize_keyboard=True
)

gender_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="👩 Женский")],
        [KeyboardButton(text="👨 Мужской")],
        [KeyboardButton(text="🚫 Отменить")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

marital_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Никогда не состоял(а) в браке")],
        [KeyboardButton(text="Разведен(а) / Вдовец(ва)")],
        [KeyboardButton(text="Предпочитаю не указывать")],
        [KeyboardButton(text="◀️ Назад")],
        [KeyboardButton(text="🚫 Отменить")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

intimate_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Да, были")],
        [KeyboardButton(text="Нет, не было")],
        [KeyboardButton(text="Не хочу отвечать")],
        [KeyboardButton(text="◀️ Назад")],
        [KeyboardButton(text="🚫 Отменить")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

religiosity_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Читаю намаз, соблюдаю пост")],
        [KeyboardButton(text="Иногда молюсь, стараюсь")],
        [KeyboardButton(text="Ищу того, кто поможет укрепить иман")],
        [KeyboardButton(text="◀️ Назад")],
        [KeyboardButton(text="🚫 Отменить")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

children_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Да, есть")],
        [KeyboardButton(text="Нет")],
        [KeyboardButton(text="Не хочу отвечать")],
        [KeyboardButton(text="◀️ Назад")],
        [KeyboardButton(text="🚫 Отменить")]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)

confirm_kb = InlineKeyboardMarkup(
    inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить", callback_data="submit")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel")]
    ]
)

# ------------------ СОСТОЯНИЯ ------------------
class RegisterForm(StatesGroup):
    gender = State()
    name = State()
    age = State()
    height_weight = State()
    city = State()
    nationality = State()
    marital = State()
    intimate = State()
    religiosity = State()
    islam_since = State()
    about = State()
    children = State()
    seeking = State()
    public_photo = State()
    private_photo = State()
    waiting_confirm = State()

# ------------------ ХЭНДЛЕРЫ РЕГИСТРАЦИИ ------------------
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Ассаламу алейкум!\n\n"
        "Я бот халяльных знакомств для никаха.\n"
        "Помогу найти серьёзные отношения.\n"
        "Чтобы начать, нажмите кнопку ниже.",
        reply_markup=main_menu_kb
    )

@dp.message(lambda msg: msg.text == "📝 Заполнить анкету")
async def start_register(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(RegisterForm.gender)
    await state.update_data(history=[])
    await message.answer(
        "📝 Давайте заполним анкету.\n\n"
        "Выберите ваш пол:",
        reply_markup=gender_kb
    )

@dp.message(lambda msg: msg.text == "❓ Помощь")
async def show_help(message: types.Message):
    await message.answer(
        "📖 Инструкция:\n"
        "1. Нажмите «Заполнить анкету»\n"
        "2. Отвечайте на вопросы\n"
        "3. В конце подтвердите анкету\n"
        "После модерации ваша анкета появится в канале.\n\n"
        "Если захотите прервать заполнение — нажмите «Отменить».",
        reply_markup=main_menu_kb
    )

@dp.message(lambda msg: msg.text == "◀️ Назад")
async def go_back(message: types.Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("Нет активного процесса регистрации.", reply_markup=main_menu_kb)
        return

    data = await state.get_data()
    history = data.get('history', [])
    if not history:
        await message.answer("Это первый шаг, назад нельзя.", reply_markup=cancel_only_kb)
        return

    prev_step = history.pop()
    await state.update_data(history=history)

    current_state_name = current_state.split(":")[-1]
    if current_state_name in data:
        del data[current_state_name]
    await state.set_data(data)

    state_map = {
        "gender": RegisterForm.gender,
        "name": RegisterForm.name,
        "age": RegisterForm.age,
        "height_weight": RegisterForm.height_weight,
        "city": RegisterForm.city,
        "nationality": RegisterForm.nationality,
        "marital": RegisterForm.marital,
        "intimate": RegisterForm.intimate,
        "religiosity": RegisterForm.religiosity,
        "islam_since": RegisterForm.islam_since,
        "about": RegisterForm.about,
        "children": RegisterForm.children,
        "seeking": RegisterForm.seeking,
        "public_photo": RegisterForm.public_photo,
        "private_photo": RegisterForm.private_photo,
    }
    prev_state = state_map.get(prev_step)
    if prev_state is None:
        await message.answer("Ошибка при возврате.", reply_markup=nav_kb)
        return

    await state.set_state(prev_state)

    question_map = {
        "gender": "Выберите ваш пол:",
        "name": "Введите ваше полное имя:",
        "age": "Укажите ваш возраст (введите число):",
        "height_weight": "Напишите ваш рост и вес (например: 165 см, 60 кг):",
        "city": "Введите название вашего города (или используйте ближайший крупный город):",
        "nationality": "Укажите вашу национальность:",
        "marital": "Укажите ваше семейное положение:",
        "intimate": "Были ли у вас близкие отношения в прошлом?",
        "religiosity": "Оцените свою религиозность:",
        "islam_since": "Расскажите, как давно вы приняли Ислам (если родились в Исламе, напишите «с рождения»):",
        "about": "Расскажите о себе (4-5 предложений):\nОпишите внешность, увлечения, работу, характер.",
        "children": "Есть ли у вас дети?",
        "seeking": "Что вы ищете в будущем супруге? Опишите идеального спутника жизни.",
        "public_photo": "📸 Отправьте своё публичное фото.\nМожно с лицом — мы автоматически закроем его стикером для сохранения анонимности.\nФото будет показываться в канале.",
        "private_photo": "📸 Теперь отправьте фото с лицом.\nОно не будет показываться в общем доступе.\nЕго смогут увидеть только те, кто проявит к вам серьёзный интерес.\nЭто обязательное поле.",
    }
    kb_map = {
        "gender": gender_kb,
        "marital": marital_kb,
        "intimate": intimate_kb,
        "religiosity": religiosity_kb,
        "children": children_kb,
    }
    reply_markup = kb_map.get(prev_step, nav_kb)
    if prev_step == "gender":
        reply_markup = gender_kb
    elif prev_step == "name":
        reply_markup = cancel_only_kb

    await message.answer(question_map.get(prev_step, "Вернулись на предыдущий шаг."), reply_markup=reply_markup)

@dp.message(lambda msg: msg.text == "🚫 Отменить")
async def cancel_register(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Действие отменено. Если передумаете — нажмите «Заполнить анкету».",
        reply_markup=main_menu_kb
    )

async def add_to_history(state: FSMContext, step_name: str):
    data = await state.get_data()
    history = data.get('history', [])
    if not history or history[-1] != step_name:
        history.append(step_name)
    await state.update_data(history=history)

# ------------------ ШАГИ РЕГИСТРАЦИИ ------------------
@dp.message(RegisterForm.gender, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_gender(message: types.Message, state: FSMContext):
    if message.text not in ["👩 Женский", "👨 Мужской"]:
        await message.answer("Пожалуйста, выберите пол, используя кнопки.", reply_markup=gender_kb)
        return
    await state.update_data(gender=message.text)
    await add_to_history(state, "gender")
    await state.set_state(RegisterForm.name)
    await message.answer("Введите ваше полное имя:", reply_markup=cancel_only_kb)

@dp.message(RegisterForm.name, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await add_to_history(state, "name")
    await state.set_state(RegisterForm.age)
    await message.answer("Укажите ваш возраст (введите число):", reply_markup=nav_kb)

@dp.message(RegisterForm.age, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Пожалуйста, введите число (например, 25)", reply_markup=nav_kb)
        return
    await state.update_data(age=int(message.text))
    await add_to_history(state, "age")
    await state.set_state(RegisterForm.height_weight)
    await message.answer("Напишите ваш рост и вес (например: 165 см, 60 кг):", reply_markup=nav_kb)

@dp.message(RegisterForm.height_weight, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_height_weight(message: types.Message, state: FSMContext):
    numbers = re.findall(r'\d+', message.text)
    if len(numbers) >= 2:
        height = int(numbers[0])
        weight = int(numbers[1])
        await state.update_data(height=height, weight=weight)
        await add_to_history(state, "height_weight")
        await state.set_state(RegisterForm.city)
        await message.answer("Введите название вашего города (или используйте ближайший крупный город):", reply_markup=nav_kb)
    else:
        await message.answer("Не удалось распознать рост и вес. Пожалуйста, введите в формате: 165 см, 60 кг", reply_markup=nav_kb)

@dp.message(RegisterForm.city, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_city(message: types.Message, state: FSMContext):
    await state.update_data(city=message.text)
    await add_to_history(state, "city")
    await state.set_state(RegisterForm.nationality)
    await message.answer("Укажите вашу национальность:", reply_markup=nav_kb)

@dp.message(RegisterForm.nationality, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_nationality(message: types.Message, state: FSMContext):
    await state.update_data(nationality=message.text)
    await add_to_history(state, "nationality")
    await state.set_state(RegisterForm.marital)
    await message.answer("Укажите ваше семейное положение:", reply_markup=marital_kb)

@dp.message(RegisterForm.marital, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_marital(message: types.Message, state: FSMContext):
    marital = message.text
    await state.update_data(marital=marital)
    await add_to_history(state, "marital")
    data = await state.get_data()
    gender = data.get('gender')
    if gender == "👩 Женский" and marital == "Никогда не состоял(а) в браке":
        await state.set_state(RegisterForm.intimate)
        await message.answer("Были ли у вас близкие отношения в прошлом?", reply_markup=intimate_kb)
    else:
        await state.update_data(intimate=None)
        await state.set_state(RegisterForm.religiosity)
        await message.answer("Оцените свою религиозность:", reply_markup=religiosity_kb)

@dp.message(RegisterForm.intimate, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_intimate(message: types.Message, state: FSMContext):
    await state.update_data(intimate=message.text)
    await add_to_history(state, "intimate")
    await state.set_state(RegisterForm.religiosity)
    await message.answer("Оцените свою религиозность:", reply_markup=religiosity_kb)

@dp.message(RegisterForm.religiosity, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_religiosity(message: types.Message, state: FSMContext):
    await state.update_data(religiosity=message.text)
    await add_to_history(state, "religiosity")
    await state.set_state(RegisterForm.islam_since)
    await message.answer(
        "Расскажите, как давно вы приняли Ислам (если родились в Исламе, напишите «с рождения»):",
        reply_markup=nav_kb
    )

@dp.message(RegisterForm.islam_since, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_islam_since(message: types.Message, state: FSMContext):
    await state.update_data(islam_since=message.text)
    await add_to_history(state, "islam_since")
    await state.set_state(RegisterForm.about)
    await message.answer(
        "Расскажите о себе (4-5 предложений):\nОпишите внешность, увлечения, работу, характер.",
        reply_markup=nav_kb
    )

@dp.message(RegisterForm.about, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_about(message: types.Message, state: FSMContext):
    await state.update_data(about=message.text)
    await add_to_history(state, "about")
    await state.set_state(RegisterForm.children)
    await message.answer(
        "Есть ли у вас дети?",
        reply_markup=children_kb
    )

@dp.message(RegisterForm.children, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_children(message: types.Message, state: FSMContext):
    await state.update_data(children=message.text)
    await add_to_history(state, "children")
    await state.set_state(RegisterForm.seeking)
    await message.answer(
        "Что вы ищете в будущем супруге? Опишите идеального спутника жизни.",
        reply_markup=nav_kb
    )

@dp.message(RegisterForm.seeking, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_seeking(message: types.Message, state: FSMContext):
    await state.update_data(seeking=message.text)
    await add_to_history(state, "seeking")
    await state.set_state(RegisterForm.public_photo)
    data = await state.get_data()
    if data.get('gender') == "👩 Женский":
        await message.answer(
            "📸 Отправьте своё публичное фото.\n"
            "Можно с лицом — мы автоматически закроем его стикером для сохранения анонимности.\n"
            "Фото будет показываться в канале.",
            reply_markup=nav_kb
        )
    else:
        await message.answer(
            "📸 Отправьте ваше фото.\n"
            "Оно будет показываться в канале.",
            reply_markup=nav_kb
        )

@dp.message(RegisterForm.public_photo, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_public_photo(message: types.Message, state: FSMContext):
    if not message.photo:
        await message.answer("Пожалуйста, отправьте именно фото (не файл и не текст).", reply_markup=nav_kb)
        return
    file_id = message.photo[-1].file_id
    await state.update_data(public_photo=file_id)
    await add_to_history(state, "public_photo")
    data = await state.get_data()
    if data.get('gender') == "👩 Женский":
        await state.set_state(RegisterForm.private_photo)
        await message.answer(
            "📸 Теперь отправьте фото с лицом.\n"
            "Оно не будет показываться в общем доступе.\n"
            "Его смогут увидеть только те, кто проявит к вам серьёзный интерес.\n"
            "Это обязательное поле.",
            reply_markup=nav_kb
        )
    else:
        await show_preview(message, state)

@dp.message(RegisterForm.private_photo, lambda msg: msg.text not in ["◀️ Назад", "🚫 Отменить"])
async def process_private_photo(message: types.Message, state: FSMContext):
    if not message.photo:
        await message.answer("Пожалуйста, отправьте фото (это обязательное поле).", reply_markup=nav_kb)
        return
    file_id = message.photo[-1].file_id
    await state.update_data(private_photo=file_id)
    await add_to_history(state, "private_photo")
    await show_preview(message, state)

# ------------------ ПОКАЗ ПРЕДПРОСМОТРА ------------------
async def show_preview(message: types.Message, state: FSMContext):
    data = await state.get_data()
    gender = data.get('gender', 'Не указан')
    
    preview = (
        f"📋 <b>Ваша анкета</b>\n\n"
        f"⚧ Пол: {gender}\n"
        f"👤 Имя: {data['name']}\n"
        f"🎂 Возраст: {data['age']}\n"
        f"📏 Рост: {data.get('height', '—')} см\n"
        f"⚖️ Вес: {data.get('weight', '—')} кг\n"
        f"📍 Город: {data['city']}\n"
        f"🌍 Национальность: {data.get('nationality', '—')}\n"
        f"💍 Семейное положение: {data['marital']}\n"
        f"🕌 Религиозность: {data['religiosity']}\n"
        f"📖 Ислам с: {data.get('islam_since', '—')}\n"
        f"📝 О себе: {data['about']}\n"
        f"👶 Дети: {data.get('children', '—')}\n"
        f"❤️ Ищу: {data['seeking']}\n"
        f"🖼 Публичное фото: есть\n"
    )
    if gender == "👩 Женский":
        preview += f"🔒 Фото с лицом: есть\n"
    else:
        preview += f"🔒 Фото с лицом: нет\n"
    preview += f"\n✅ Всё верно? Нажмите «Подтвердить», чтобы отправить на модерацию."

    await message.answer(preview, parse_mode="HTML", reply_markup=confirm_kb)
    await state.set_state(RegisterForm.waiting_confirm)

# ------------------ ОТПРАВКА АДМИНУ И МОДЕРАЦИЯ ------------------
@dp.callback_query(lambda c: c.data == "submit", StateFilter(RegisterForm.waiting_confirm))
async def submit_anketa(callback: types.CallbackQuery, state: FSMContext):
    try:
        await callback.answer()
        data = await state.get_data()
        user_id = callback.from_user.id
        gender = data.get('gender')

        if gender == "👩 Женский":
            marital = data.get('marital')
            intimate = data.get('intimate')
            if marital == "Никогда не состоял(а) в браке" and intimate == "Нет, не было":
                price_category = "1400"
            else:
                price_category = "700"
        else:
            price_category = "0"

        data['price_category'] = price_category
        save_anketa(data, user_id)

        asyncio.create_task(send_to_admin(data, user_id))

        await callback.message.edit_text(
            "🙌 Спасибо! Ваша анкета отправлена на модерацию.\n"
            "Мы свяжемся с вами в ближайшее время.",
            reply_markup=None
        )
        await state.clear()

    except Exception as e:
        logging.error(f"Ошибка в submit_anketa: {e}")
        await callback.message.edit_text("❌ Произошла ошибка при отправке анкеты. Попробуйте позже.")
        await callback.answer()

async def send_to_admin(data: dict, user_id: int):
    try:
        gender = data.get('gender')
        text = (
            f"📋 <b>Новая анкета</b>\n\n"
            f"⚧ Пол: {gender}\n"
            f"👤 Имя: {data['name']}\n"
            f"🎂 Возраст: {data['age']}\n"
            f"📏 Рост: {data.get('height', '—')} см\n"
            f"⚖️ Вес: {data.get('weight', '—')} кг\n"
            f"📍 Город: {data['city']}\n"
            f"🌍 Национальность: {data.get('nationality', '—')}\n"
            f"💍 Семейное положение: {data['marital']}\n"
            f"🕌 Религиозность: {data['religiosity']}\n"
            f"📖 Ислам с: {data.get('islam_since', '—')}\n"
            f"📝 О себе: {data['about']}\n"
            f"👶 Дети: {data.get('children', '—')}\n"
            f"❤️ Ищу: {data['seeking']}\n"
        )
        if gender == "👩 Женский":
            text += f"🔒 Фото с лицом: есть\n"
            text += f"💰 Цена: {data.get('price_category', '700')} руб.\n"
        else:
            text += f"🔒 Фото с лицом: нет\n"

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{user_id}")],
                [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{user_id}")]
            ]
        )

        public_photo = data.get('public_photo')
        if public_photo:
            await bot.send_photo(
                chat_id=ADMIN_ID,
                photo=public_photo,
                caption=text,
                parse_mode="HTML",
                reply_markup=kb
            )
        else:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=text + "\n❌ Фото отсутствует!",
                parse_mode="HTML",
                reply_markup=kb
            )
    except Exception as e:
        logging.error(f"Ошибка при отправке админу: {e}")

# ------------------ ОБРАБОТКА РЕШЕНИЙ АДМИНА ------------------
@dp.callback_query(lambda c: c.data.startswith("approve_") and not c.data.startswith("approvelike_"))
async def approve_anketa(callback: types.CallbackQuery):
    try:
        await callback.answer()
        user_id = int(callback.data.split("_")[1])
        row = get_anketa(user_id)
        if not row:
            await callback.message.edit_caption(
                caption="❌ Анкета не найдена или уже обработана.",
                reply_markup=None
            )
            return

        data = {
            'user_id': row[1],
            'gender': row[2],
            'name': row[3],
            'age': row[4],
            'height': row[5],
            'weight': row[6],
            'city': row[7],
            'marital': row[8],
            'intimate': row[9],
            'religiosity': row[10],
            'about': row[11],
            'seeking': row[12],
            'public_photo': row[13],
            'private_photo': row[14],
            'price_category': row[15],
            'nationality': row[19] if len(row) > 19 else None,
            'islam_since': row[20] if len(row) > 20 else None,
            'children': row[21] if len(row) > 21 else None,
        }

        update_status(user_id, 'approved')

        asyncio.create_task(publish_to_channel(data))
        asyncio.create_task(notify_user(user_id, "✅ Ваша анкета одобрена и опубликована в канале! Желаем удачи в поиске спутника жизни."))

        await callback.message.edit_caption(
            caption=f"✅ Анкета пользователя {data['name']} одобрена и опубликована в канале.",
            reply_markup=None
        )

    except Exception as e:
        logging.error(f"Ошибка в approve_anketa: {e}")
        try:
            await callback.message.edit_caption(
                caption="❌ Произошла ошибка при одобрении анкеты.",
                reply_markup=None
            )
        except:
            pass
        await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("reject_") and not c.data.startswith("rejectlike_"))
async def reject_anketa(callback: types.CallbackQuery):
    try:
        await callback.answer()
        user_id = int(callback.data.split("_")[1])
        update_status(user_id, 'rejected')

        await callback.message.edit_caption(
            caption="❌ Анкета отклонена.",
            reply_markup=None
        )

        asyncio.create_task(notify_user(user_id, "❌ Ваша анкета не прошла модерацию. Пожалуйста, проверьте правильность заполнения и попробуйте снова."))

    except Exception as e:
        logging.error(f"Ошибка в reject_anketa: {e}")
        try:
            await callback.message.edit_caption(
                caption="❌ Произошла ошибка при отклонении анкеты.",
                reply_markup=None
            )
        except:
            pass
        await callback.answer()

async def notify_user(user_id: int, text: str):
    try:
        await bot.send_message(user_id, text)
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление пользователю {user_id}: {e}")

# ------------------ ПУБЛИКАЦИЯ В КАНАЛ (только лайк) ------------------
async def publish_to_channel(data: dict):
    name = data.get('name', '')
    if name:
        name = name[0].upper() + name[1:] if len(name) > 1 else name.upper()
    
    age = data.get('age', '—')
    height = data.get('height', '—')
    weight = data.get('weight', '—')
    city = data.get('city', '—')
    nationality = data.get('nationality', '—')
    marital = data.get('marital', '—')
    religiosity = data.get('religiosity', '—')
    islam_since = data.get('islam_since', '—')
    about = data.get('about', '—')
    children = data.get('children', '—')
    seeking = data.get('seeking', '—')
    user_id = data.get('user_id')
    
    text = (
        f"👤 Имя: {name}, {age} лет\n"
        f"📏 Рост: {height} см, Вес: {weight} кг\n"
        f"📍 Город: {city}\n"
        f"🌍 Национальность: {nationality}\n"
        f"💍 Семейное положение: {marital}\n"
        f"🕌 Религиозность: {religiosity}\n"
        f"📖 Ислам с: {islam_since}\n"
        f"📝 О себе: {about}\n"
        f"👶 Дети: {children}\n"
        f"❤️ Ищу: {seeking}\n"
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="❤️ Лайк", callback_data=f"like_{user_id}")
    builder.adjust(1)

    public_photo = data.get('public_photo')
    try:
        if public_photo:
            sent = await bot.send_photo(
                chat_id=CHANNEL_ID,
                photo=public_photo,
                caption=text,
                parse_mode="HTML",
                reply_markup=builder.as_markup()
            )
        else:
            sent = await bot.send_message(
                chat_id=CHANNEL_ID,
                text=text,
                parse_mode="HTML",
                reply_markup=builder.as_markup()
            )
        update_post_message_id(user_id, sent.message_id)
    except Exception as e:
        logging.error(f"Ошибка публикации в канал: {e}")
        await bot.send_message(ADMIN_ID, f"❌ Ошибка публикации в канал: {e}")

# ------------------ ЛАЙКИ (с кнопкой покупки фото) ------------------
@dp.callback_query(lambda c: c.data and c.data.startswith("like_"))
@dp.callback_query(lambda c: c.data and c.data.startswith("like_"))
async def process_like(callback: types.CallbackQuery):
    try:
        await callback.answer()
        target_user_id = int(callback.data.split("_")[1])
        from_user_id = callback.from_user.id

        if from_user_id == target_user_id:
            await bot.send_message(from_user_id, "❌ Нельзя лайкать свою анкету.")
            return

        target_data = get_user_by_id(target_user_id)
        if not target_data or target_data[16] != 'approved':
            await bot.send_message(from_user_id, "❌ Анкета не найдена или ещё не опубликована.")
            return

        # Проверяем взаимность
        mutual = get_any_like_between(from_user_id, target_user_id)
        if mutual:
            from_data = get_user_by_id(from_user_id)
            if from_data and "Мужской" in from_data[2]:
                await bot.send_message(from_user_id, "❤️ У вас уже есть взаимность с этим пользователем. Вы можете оплатить контакт.")
            else:
                await bot.send_message(from_user_id, "❤️ Вы уже одобрили этого пользователя. Взаимность установлена.")
            return

        existing = get_like(from_user_id, target_user_id)
        if existing:
            status = existing[3]
            if status == 'pending':
                await bot.send_message(from_user_id, "⏳ Вы уже отправили запрос этому пользователю. Ожидайте ответа.")
            elif status == 'approved':
                await bot.send_message(from_user_id, "❤️ Взаимность уже подтверждена! Вы можете оплатить контакт.")
            elif status == 'rejected':
                await bot.send_message(from_user_id, "❌ Пользователь отклонил ваш запрос ранее.")
            return

        from_data = get_user_by_id(from_user_id)
        if not from_data:
            await bot.send_message(from_user_id, "❌ Ваша анкета не найдена. Заполните анкету через /start.")
            return

        if not save_like(from_user_id, target_user_id):
            await bot.send_message(from_user_id, "❌ Ошибка при сохранении лайка.")
            return

        target_gender = target_data[2]
        if "Женский" in target_gender:
            recipient_text = "девушке"
        else:
            recipient_text = "мужчине"

        gender_emoji_sender = "👨" if from_data[2] == "👨 Мужской" else "👩"
        sender_text = (
            f"📋 <b>Вам поступил запрос</b>\n\n"
            f"{gender_emoji_sender} Имя: {from_data[3]}\n"
            f"🎂 Возраст: {from_data[4]}\n"
            f"📏 Рост: {from_data[5]} см, Вес: {from_data[6]} кг\n"
            f"📍 Город: {from_data[7]}\n"
            f"🌍 Национальность: {from_data[19] if len(from_data) > 19 else '—'}\n"
            f"🕌 Религиозность: {from_data[10]}\n"
            f"📖 Ислам с: {from_data[20] if len(from_data) > 20 else '—'}\n"
            f"📝 О себе: {from_data[11]}\n"
            f"👶 Дети: {from_data[21] if len(from_data) > 21 else '—'}\n"
            f"❤️ Ищет: {from_data[12]}\n"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approvelike_{from_user_id}_{target_user_id}")],
                [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"rejectlike_{from_user_id}_{target_user_id}")]
            ]
        )
        public_photo_sender = from_data[13] if len(from_data) > 13 else None
        if public_photo_sender:
            await bot.send_photo(
                chat_id=target_user_id,
                photo=public_photo_sender,
                caption=sender_text,
                parse_mode="HTML",
                reply_markup=kb
            )
        else:
            await bot.send_message(
                chat_id=target_user_id,
                text=sender_text + "\n❌ Фото отсутствует.",
                parse_mode="HTML",
                reply_markup=kb
            )

        # Сообщение мужчине с кнопкой покупки фото (отправляем в личку)
        woman_data = get_user_by_id(target_user_id)
        woman_name = woman_data[3] if woman_data else ""
        woman_name = woman_name[0].upper() + woman_name[1:] if woman_name else "девушки"
        photo_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="👀 Купить фото за 100 руб.", callback_data=f"buyphoto_{target_user_id}_{from_user_id}")]
            ]
        )
        await bot.send_message(
            chat_id=from_user_id,
            text=f"✅ Запрос отправлен {recipient_text}. Ожидайте ответа.\n\n"
                 f"Если вы хотите увидеть фото с лицом {woman_name} прямо сейчас, вы можете купить его за 100 руб. Оно будет доступно только вам.",
            reply_markup=photo_kb
        )

    except Exception as e:
        logging.error(f"Ошибка в process_like: {e}")
        await bot.send_message(from_user_id, "❌ Произошла ошибка. Попробуйте позже.")

# ------------------ ОДОБРЕНИЕ ЛАЙКА ------------------
@dp.callback_query(lambda c: c.data and c.data.startswith("approvelike_"))
async def approve_like(callback: types.CallbackQuery):
    try:
        await callback.answer()
        parts = callback.data.split("_")
        # parts: ["approvelike", "from_user_id", "to_user_id"]
        from_user_id = int(parts[1])
        to_user_id = int(parts[2])

        update_like_status(from_user_id, to_user_id, 'approved')

        woman_data = get_user_by_id(to_user_id)
        if not woman_data:
            await callback.message.delete()
            return

        name = woman_data[3] or "—"
        if name:
            name = name[0].upper() + name[1:] if len(name) > 1 else name.upper()
        age = woman_data[4] or "—"
        height = woman_data[5] or "—"
        weight = woman_data[6] or "—"
        city = woman_data[7] or "—"
        nationality = woman_data[19] if len(woman_data) > 19 and woman_data[19] else "—"
        marital = woman_data[8] or "—"
        religiosity = woman_data[10] or "—"
        islam_since = woman_data[20] if len(woman_data) > 20 and woman_data[20] else "—"
        about = woman_data[11] or "—"
        children = woman_data[21] if len(woman_data) > 21 and woman_data[21] else "—"
        seeking = woman_data[12] or "—"
        public_photo = woman_data[13] if len(woman_data) > 13 else None

        woman_text = (
            f"👤 Имя: {name}, {age} лет\n"
            f"📏 Рост: {height} см, Вес: {weight} кг\n"
            f"📍 Город: {city}\n"
            f"🌍 Национальность: {nationality}\n"
            f"💍 Семейное положение: {marital}\n"
            f"🕌 Религиозность: {religiosity}\n"
            f"📖 Ислам с: {islam_since}\n"
            f"📝 О себе: {about}\n"
            f"👶 Дети: {children}\n"
            f"❤️ Ищу: {seeking}\n"
        )

        price_category = woman_data[15] if len(woman_data) > 15 and woman_data[15] else "700"
        try:
            price = int(price_category)
        except:
            price = 700

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=f"💳 Оплатить контакт ({price} руб.)", callback_data=f"paycontact_{to_user_id}_{from_user_id}")]
            ]
        )
        if public_photo:
            await bot.send_photo(
                chat_id=from_user_id,
                photo=public_photo,
                caption=f"❤️ Девушка одобрила ваш запрос!\n\n{woman_text}\n\n💳 Теперь вы можете оплатить контакт и начать общение.\nСтоимость: {price} руб.",
                parse_mode="HTML",
                reply_markup=kb
            )
        else:
            await bot.send_message(
                chat_id=from_user_id,
                text=f"❤️ Девушка одобрила ваш запрос!\n\n{woman_text}\n\n💳 Теперь вы можете оплатить контакт и начать общение.\nСтоимость: {price} руб.",
                parse_mode="HTML",
                reply_markup=kb
            )

        # Удаляем сообщение с кнопками у девушки
        await callback.message.delete()
        # Отправляем девушке подтверждение с именем мужчины
        from_data = get_user_by_id(from_user_id)
        man_name = from_data[3] if from_data else "пользователь"
        await callback.message.answer(
            f"✅ Вы одобрили запрос от \"{man_name}\". Мужчина получит вашу анкету и сможет связаться с вами."
        )

    except Exception as e:
        logging.error(f"Ошибка в approve_like: {e}")
        try:
            await callback.message.delete()
        except:
            pass
        await callback.answer()

# ------------------ ОТКЛОНЕНИЕ ЛАЙКА ------------------
@dp.callback_query(lambda c: c.data and c.data.startswith("rejectlike_"))
async def reject_like(callback: types.CallbackQuery):
    try:
        await callback.answer()
        parts = callback.data.split("_")
        from_user_id = int(parts[1])
        to_user_id = int(parts[2])

        update_like_status(from_user_id, to_user_id, 'rejected')

        try:
            await bot.send_message(
                chat_id=from_user_id,
                text="❌ Пользователь отклонил ваш запрос."
            )
        except Exception as e:
            logging.error(f"Не удалось отправить сообщение отправителю {from_user_id}: {e}")

        # Удаляем сообщение с кнопками у девушки
        await callback.message.delete()
        # Отправляем девушке подтверждение с именем мужчины
        from_data = get_user_by_id(from_user_id)
        man_name = from_data[3] if from_data else "пользователь"
        await callback.message.answer(
            f"❌ Вы отклонили запрос от \"{man_name}\"."
        )

    except Exception as e:
        logging.error(f"Ошибка в reject_like: {e}")
        try:
            await callback.message.delete()
        except:
            pass
        await callback.answer()

# ------------------ ФУНКЦИИ ДЛЯ ОПЛАТЫ ЧЕРЕЗ API ЮKASSA ------------------
async def create_yookassa_payment(amount: float, description: str, user_id: int, service_type: str, service_data: str = ""):
    """
    Создаёт платёж в ЮKassa и возвращает ссылку для оплаты.
    """
    try:
        # Создаём платёж с фиксированным return_url (юзернейм вашего бота)
        payment = Payment.create({
            "amount": {
                "value": f"{amount:.2f}",
                "currency": "RUB"
            },
            "confirmation": {
                "type": "redirect",
                "return_url": "https://t.me/MuslimkaNikahBot"  # замените на ваш юзернейм бота, если он отличается
            },
            "capture": True,
            "description": description,
            "metadata": {
                "user_id": str(user_id),
                "service_type": service_type,
                "service_data": service_data
            }
        })
        payment_id = payment.id
        save_pending_payment(payment_id, user_id, service_type, service_data)
        return payment.confirmation.confirmation_url
    except Exception as e:
        logging.error(f"Ошибка создания платежа ЮKassa: {e}")
        return None

async def process_successful_payment(payment_id: str, user_id: int, service_type: str, service_data: str):
    """
    Обрабатывает успешный платёж: предоставляет услугу.
    """
    try:
        if service_type == "contact":
            # service_data содержит seller_id и buyer_id через запятую
            parts = service_data.split(",")
            if len(parts) == 2:
                seller_id = int(parts[0])
                buyer_id = int(parts[1])
                # Создаём чат
                chat = await bot.create_supergroup(
                    title=f"🤝 Знакомство",
                    user_ids=[buyer_id, seller_id]
                )
                chat_id = chat.id
                expires_at = datetime.now() + timedelta(hours=72)
                save_paid_contact(buyer_id, seller_id, chat_id, expires_at)
                await bot.send_message(
                    chat_id=chat_id,
                    text="👋 Добро пожаловать в чат знакомства!\n"
                         "Чат будет активен 72 часа. После этого он будет удалён.\n"
                         "Пожалуйста, соблюдайте этикет и уважайте друг друга."
                )
                await bot.send_message(buyer_id, "✅ Оплата прошла успешно! Чат создан. Вы можете общаться.")
                await bot.send_message(seller_id, "✅ Пользователь оплатил контакт. Чат создан. Вы можете общаться.")
                # Удаляем из pending
                delete_pending_payment(payment_id)
        elif service_type == "photo":
            # service_data содержит seller_id и buyer_id через запятую
            parts = service_data.split(",")
            if len(parts) == 2:
                seller_id = int(parts[0])
                buyer_id = int(parts[1])
                woman_data = get_user_by_id(seller_id)
                if woman_data:
                    private_photo = woman_data[14] if len(woman_data) > 14 else None
                    if private_photo:
                        await bot.send_photo(
                            chat_id=buyer_id,
                            photo=private_photo,
                            caption="📸 Ваше фото. Оно не сохраняется у бота и будет доступно только сейчас."
                        )
                        await bot.send_message(buyer_id, "✅ Фото отправлено!")
                    else:
                        await bot.send_message(buyer_id, "❌ Приватное фото отсутствует.")
                delete_pending_payment(payment_id)
        else:
            logging.warning(f"Неизвестный тип услуги: {service_type}")
    except Exception as e:
        logging.error(f"Ошибка в process_successful_payment: {e}")

# ------------------ ОПЛАТА: КОНТАКТ (через API ЮKassa) ------------------
@dp.callback_query(lambda c: c.data and c.data.startswith("paycontact_"))
async def process_pay_contact(callback: types.CallbackQuery):
    try:
        await callback.answer()
        parts = callback.data.split("_")
        seller_id = int(parts[1])
        buyer_id = int(parts[2])

        if callback.from_user.id != buyer_id:
            await callback.message.answer("❌ Это не ваша кнопка.")
            return

        woman_data = get_user_by_id(seller_id)
        if not woman_data:
            await callback.message.answer("❌ Анкета девушки не найдена.")
            return

        price_category = woman_data[15] if len(woman_data) > 15 and woman_data[15] else "700"
        try:
            price = int(price_category)
        except:
            price = 700

        # Создаём платёж через API ЮKassa
        service_data = f"{seller_id},{buyer_id}"
        payment_url = await create_yookassa_payment(
            amount=float(price),
            description=f"Доступ к контакту",
            user_id=buyer_id,
            service_type="contact",
            service_data=service_data
        )
        if payment_url:
            pay_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text=f"💳 Оплатить {price} руб.", url=payment_url)]
                ]
            )
            await callback.message.answer(
                f"💳 Для оплаты контакта нажмите кнопку ниже.\nСумма: {price} руб.\n\nПосле оплаты чат будет создан автоматически.",
                reply_markup=pay_kb
            )
            await callback.message.delete()
        else:
            await callback.message.answer("❌ Ошибка при создании платежа. Попробуйте позже.")

    except Exception as e:
        logging.error(f"Ошибка в process_pay_contact: {e}")
        await callback.message.answer("❌ Ошибка при создании счёта. Попробуйте позже.")

# ------------------ ОПЛАТА: ФОТО (через API ЮKassa) ------------------
@dp.callback_query(lambda c: c.data and c.data.startswith("buyphoto_"))
async def buy_photo(callback: types.CallbackQuery):
    try:
        await callback.answer()
        parts = callback.data.split("_")
        target_user_id = int(parts[1])
        buyer_id = int(parts[2])

        woman_data = get_user_by_id(target_user_id)
        if not woman_data:
            await callback.message.answer("❌ Анкета не найдена.")
            return

        private_photo = woman_data[14] if len(woman_data) > 14 else None
        if not private_photo:
            await callback.message.answer("❌ У этой девушки нет приватного фото.")
            return

        # Создаём платёж через API ЮKassa
        service_data = f"{target_user_id},{buyer_id}"
        payment_url = await create_yookassa_payment(
            amount=100.0,
            description="Просмотр приватного фото",
            user_id=buyer_id,
            service_type="photo",
            service_data=service_data
        )
        if payment_url:
            pay_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Оплатить 100 руб.", url=payment_url)]
                ]
            )
            await callback.message.answer(
                "💳 Для покупки фото нажмите кнопку ниже.\nСумма: 100 руб.\n\nПосле оплаты фото будет отправлено.",
                reply_markup=pay_kb
            )
        else:
            await callback.message.answer("❌ Ошибка при создании платежа. Попробуйте позже.")

    except Exception as e:
        logging.error(f"Ошибка в buy_photo: {e}")
        await callback.message.answer("❌ Ошибка. Попробуйте позже.")

# ------------------ ТЕСТОВАЯ КОМАНДА ОПЛАТЫ ЧЕРЕЗ API ------------------
@dp.message(Command("testpay"))
async def test_pay(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ У вас нет прав.")
        return
    try:
        payment_url = await create_yookassa_payment(
            amount=10.0,
            description="Тестовый платёж 10 рублей",
            user_id=message.from_user.id,
            service_type="test",
            service_data="test"
        )
        if payment_url:
            pay_kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Оплатить 10 руб.", url=payment_url)]
                ]
            )
            await message.answer(
                "💳 Тестовый платёж на 10 рублей.\nНажмите кнопку для оплаты.",
                reply_markup=pay_kb
            )
        else:
            await message.answer("❌ Ошибка при создании тестового платежа.")
    except Exception as e:
        logging.error(f"Ошибка в test_pay: {e}")
        await message.answer(f"❌ Ошибка: {e}")

# ------------------ WEBHOOK-СЕРВЕР ДЛЯ ПРИЁМА УВЕДОМЛЕНИЙ ОТ ЮKASSA ------------------
async def handle_yookassa_webhook(request):
    try:
        data = await request.json()
        logging.info(f"Получен webhook: {data}")

        # Проверяем, что это уведомление об успешной оплате
        if data.get('event') == 'payment.succeeded':
            payment = data.get('object')
            payment_id = payment.get('id')
            metadata = payment.get('metadata', {})
            user_id = int(metadata.get('user_id', 0))
            service_type = metadata.get('service_type', '')
            service_data = metadata.get('service_data', '')

            if payment_id and user_id:
                # Проверяем, не обрабатывали ли уже этот платёж
                pending = get_pending_payment(payment_id)
                if pending:
                    await process_successful_payment(payment_id, user_id, service_type, service_data)
                else:
                    logging.warning(f"Платёж {payment_id} уже обработан или не найден.")
        return web.Response(status=200, text="OK")
    except Exception as e:
        logging.error(f"Ошибка в webhook: {e}")
        return web.Response(status=500, text="Internal Server Error")

async def start_webhook_server():
    """Запускает aiohttp сервер для приёма webhook-уведомлений."""
    app = web.Application()
    app.router.add_post('/yookassa_webhook', handle_yookassa_webhook)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get('PORT', 8080))  # Render задаёт PORT
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"Webhook-сервер запущен на порту {port}")
    return runner, site

# ------------------ ЗАПУСК БОТА И WEBHOOK-СЕРВЕРА ------------------
async def main():
    print("✅ Бот запущен!")
    # Запускаем webhook-сервер
    runner, site = await start_webhook_server()
    # Запускаем бота с поллингом
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Бот остановлен.")