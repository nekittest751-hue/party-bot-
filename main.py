import telebot
from telebot import types
import json
import os

TOKEN = os.getenv("BOT_TOKEN")
bot = telebot.TeleBot(TOKEN)

DB_FILE = "db.json"

# =====================
# БАЗА
# =====================
def load_db():
    if not os.path.exists(DB_FILE):
        return {"users": {}}
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_db(db):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)

def get_user(db, user):
    uid = str(user.id)
    if uid not in db["users"]:
        db["users"][uid] = {
            "username": user.username,
            "sub": "FREE"
        }
    return db["users"][uid]

# =====================
# UI
# =====================
def main_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🎉 Комнаты", callback_data="rooms"))
    kb.add(types.InlineKeyboardButton("💎 Подписка", callback_data="sub"))
    kb.add(types.InlineKeyboardButton("👤 Профиль", callback_data="profile"))
    return kb

def rooms_menu():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("➕ Создать комнату", callback_data="create_room"))
    kb.add(types.InlineKeyboardButton("📋 Список комнат", callback_data="list_rooms"))
    kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back"))
    return kb

# =====================
# СТАРТ
# =====================
@bot.message_handler(commands=['start'])
def start(msg):
    db = load_db()
    get_user(db, msg.from_user)
    save_db(db)

    bot.send_message(
        msg.chat.id,
        "🥳 PARTY BOT PRO\n\nГлавное меню:",
        reply_markup=main_menu()
    )

# =====================
# CALLBACK HANDLER
# =====================
@bot.callback_query_handler(func=lambda call: True)
def callback(call):
    db = load_db()
    user = get_user(db, call.from_user)

    # ===== ГЛАВНОЕ =====
    if call.data == "rooms":
        bot.edit_message_text(
            "🎉 Управление комнатами:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=rooms_menu()
        )

    elif call.data == "sub":
        text = f"""
💎 Подписки:

FREE (0₽):
- ник виден
- лимиты
- 5ч/день
- задержка 500мс
- комнаты до 10 чел (3/нед)

NORM (59₽):
- без лимитов FREE
- 3ч VIP/день
- 15 чел
- 300мс
- медиа
- другие сервера

VIP (299₽):
- безлимит VIP
- анонимный ник
- 25 чел
- без задержки
- игры / опросы
- защита комнаты

BUSINESS (1999₽):
- всё без лимитов
- комнаты до 100 чел
- приглашения
"""

        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔥 Получить VIP (тест)", callback_data="get_vip"))
        kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back"))

        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)

    elif call.data == "profile":
        text = f"""
👤 Профиль:

Ник: @{user['username']}
Подписка: {user['sub']}
"""
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="back"))

        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)

    # ===== КОМНАТЫ =====
    elif call.data == "create_room":
        bot.answer_callback_query(call.id, "🚧 Скоро будет создание комнат")

    elif call.data == "list_rooms":
        bot.answer_callback_query(call.id, "🚧 Пока пусто")

    # ===== ТЕСТ VIP =====
    elif call.data == "get_vip":
        user["sub"] = "VIP"
        save_db(db)

        bot.answer_callback_query(call.id, "🔥 VIP выдан (тест)")
        bot.edit_message_text(
            "✅ Теперь у тебя VIP!",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=main_menu()
        )

    # ===== НАЗАД =====
    elif call.data == "back":
        bot.edit_message_text(
            "🏠 Главное меню:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=main_menu()
        )

# =====================
# ЗАПУСК
# =====================
bot.infinity_polling()
from flask import Flask
import threading
import os

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

def run_bot():
    bot.infinity_polling()

if __name__ == "__main__":
    threading.Thread(target=run_bot).start()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
