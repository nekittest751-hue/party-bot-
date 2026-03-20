import os
import re
import json
import time
import sqlite3
import threading
from datetime import datetime, date

from flask import Flask, request
import telebot
from telebot import types

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

ADMIN_IDS = {
    int(x)
    for x in re.split(r"[,\s]+", os.getenv("ADMIN_IDS", "").strip())
    if x.isdigit()
}

DB_PATH = os.getenv("DB_PATH", "party_bot.sqlite3")
PORT = int(os.getenv("PORT", "10000"))

PLAN_ORDER = {"FREE": 0, "NORM": 1, "VIP": 2, "BUSINESS": 3}

PLAN_CONFIG = {
    "FREE": {
        "display_name": "FREE 0₽/мес",
        "visible_nick": True,
        "anonymous_nick": False,
        "message_delay_ms": 500,
        "room_create_weekly": 3,
        "public_room_max": 10,
        "vip_room_max": 15,
        "vip_voice_daily_hours": 1,
        "public_voice_daily_hours": 5,
        "can_send_media": False,
        "can_join_external_servers": False,
        "can_auto_choose_rooms": False,
        "room_limit_free": 3,
        "room_limit_vip": 0,
        "room_popularity_weekly": 0,
        "can_invite_people": 0,
        "unlimited_rooms": False,
    },
    "NORM": {
        "display_name": "NORM 59₽/мес",
        "visible_nick": True,
        "anonymous_nick": False,
        "message_delay_ms": 300,
        "room_create_weekly": None,
        "public_room_max": 15,
        "vip_room_max": 15,
        "vip_voice_daily_hours": 3,
        "public_voice_daily_hours": None,
        "can_send_media": True,
        "can_join_external_servers": True,
        "can_auto_choose_rooms": True,
        "room_limit_free": 0,
        "room_limit_vip": 0,
        "room_popularity_weekly": 0,
        "can_invite_people": 0,
        "unlimited_rooms": False,
    },
    "VIP": {
        "display_name": "VIP 299₽/мес",
        "visible_nick": False,
        "anonymous_nick": True,
        "message_delay_ms": 0,
        "room_create_weekly": 50,
        "public_room_max": 25,
        "vip_room_max": 15,
        "vip_voice_daily_hours": None,
        "public_voice_daily_hours": None,
        "can_send_media": True,
        "can_join_external_servers": True,
        "can_auto_choose_rooms": True,
        "room_limit_free": 0,
        "room_limit_vip": 0,
        "room_popularity_weekly": 50,
        "can_invite_people": 0,
        "unlimited_rooms": False,
    },
    "BUSINESS": {
        "display_name": "BUSINESS 1999₽/мес",
        "visible_nick": False,
        "anonymous_nick": True,
        "message_delay_ms": 0,
        "room_create_weekly": None,
        "public_room_max": 100,
        "vip_room_max": 100,
        "vip_voice_daily_hours": None,
        "public_voice_daily_hours": None,
        "can_send_media": True,
        "can_join_external_servers": True,
        "can_auto_choose_rooms": True,
        "room_limit_free": 0,
        "room_limit_vip": 0,
        "room_popularity_weekly": None,
        "can_invite_people": 25,
        "unlimited_rooms": True,
    },
}

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", threaded=True)
app = Flask(__name__)
lock = threading.RLock()

# ---------------------
# DB
# ---------------------
def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                plan TEXT NOT NULL DEFAULT 'FREE',
                active_room_id INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rooms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'public',
                max_members INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                is_open INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS room_members (
                room_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                joined_at TEXT NOT NULL,
                PRIMARY KEY (room_id, user_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                room_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plan_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_daily_usage (
                user_id INTEGER NOT NULL,
                day TEXT NOT NULL,
                text_count INTEGER NOT NULL DEFAULT 0,
                voice_seconds INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, day)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_weekly_usage (
                user_id INTEGER NOT NULL,
                week TEXT NOT NULL,
                rooms_created INTEGER NOT NULL DEFAULT 0,
                popularity_points INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, week)
            )
        """)

def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")

def iso_day():
    return date.today().isoformat()

def iso_week():
    y, w, _ = date.today().isocalendar()
    return f"{y}-W{w:02d}"

def get_or_create_user(user):
    with lock, connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone()
        if row:
            conn.execute(
                "UPDATE users SET username = ?, first_name = ? WHERE user_id = ?",
                (user.username or "", user.first_name or "", user.id),
            )
            conn.commit()
            return dict(row)
        conn.execute(
            "INSERT INTO users(user_id, username, first_name, plan, active_room_id, created_at) VALUES (?, ?, ?, 'FREE', NULL, ?)",
            (user.id, user.username or "", user.first_name or "", now_iso()),
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM users WHERE user_id = ?", (user.id,)).fetchone())

def get_user_by_id(user_id):
    with lock, connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

def set_user_plan(user_id, plan):
    plan = plan.upper()
    if plan not in PLAN_CONFIG:
        raise ValueError("unknown plan")
    with lock, connect() as conn:
        conn.execute("UPDATE users SET plan = ? WHERE user_id = ?", (plan, user_id))
        conn.execute(
            "INSERT INTO plan_logs(user_id, action, created_at) VALUES (?, ?, ?)",
            (user_id, f"set_plan:{plan}", now_iso()),
        )
        conn.commit()

def set_active_room(user_id, room_id):
    with lock, connect() as conn:
        conn.execute("UPDATE users SET active_room_id = ? WHERE user_id = ?", (room_id, user_id))
        conn.commit()

def ensure_usage(user_id):
    d = iso_day()
    w = iso_week()
    with lock, connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO user_daily_usage(user_id, day, text_count, voice_seconds) VALUES (?, ?, 0, 0)",
            (user_id, d),
        )
        conn.execute(
            "INSERT OR IGNORE INTO user_weekly_usage(user_id, week, rooms_created, popularity_points) VALUES (?, ?, 0, 0)",
            (user_id, w),
        )
        conn.commit()

def get_plan(user):
    return PLAN_CONFIG.get((user or {}).get("plan", "FREE"), PLAN_CONFIG["FREE"])

def get_rooms_created_this_week(user_id):
    ensure_usage(user_id)
    with lock, connect() as conn:
        row = conn.execute(
            "SELECT rooms_created FROM user_weekly_usage WHERE user_id = ? AND week = ?",
            (user_id, iso_week()),
        ).fetchone()
        return row["rooms_created"] if row else 0

def incr_rooms_created(user_id):
    ensure_usage(user_id)
    with lock, connect() as conn:
        conn.execute(
            """
            UPDATE user_weekly_usage
            SET rooms_created = rooms_created + 1
            WHERE user_id = ? AND week = ?
            """,
            (user_id, iso_week()),
        )
        conn.commit()

def incr_popularity(user_id, points=1):
    ensure_usage(user_id)
    with lock, connect() as conn:
        conn.execute(
            """
            UPDATE user_weekly_usage
            SET popularity_points = popularity_points + ?
            WHERE user_id = ? AND week = ?
            """,
            (points, user_id, iso_week()),
        )
        conn.commit()

def today_usage(user_id):
    ensure_usage(user_id)
    with lock, connect() as conn:
        row = conn.execute(
            "SELECT * FROM user_daily_usage WHERE user_id = ? AND day = ?",
            (user_id, iso_day()),
        ).fetchone()
        return dict(row) if row else {"text_count": 0, "voice_seconds": 0}

def add_text_usage(user_id, amount=1):
    ensure_usage(user_id)
    with lock, connect() as conn:
        conn.execute(
            """
            UPDATE user_daily_usage
            SET text_count = text_count + ?
            WHERE user_id = ? AND day = ?
            """,
            (amount, user_id, iso_day()),
        )
        conn.commit()

def add_voice_usage(user_id, seconds):
    ensure_usage(user_id)
    with lock, connect() as conn:
        conn.execute(
            """
            UPDATE user_daily_usage
            SET voice_seconds = voice_seconds + ?
            WHERE user_id = ? AND day = ?
            """,
            (int(seconds), user_id, iso_day()),
        )
        conn.commit()

def create_room(owner_id, name, kind, max_members):
    with lock, connect() as conn:
        cur = conn.execute(
            "INSERT INTO rooms(owner_id, name, kind, max_members, created_at, is_open) VALUES (?, ?, ?, ?, ?, 1)",
            (owner_id, name, kind, int(max_members), now_iso()),
        )
        room_id = cur.lastrowid
        conn.execute(
            "INSERT OR IGNORE INTO room_members(room_id, user_id, joined_at) VALUES (?, ?, ?)",
            (room_id, owner_id, now_iso()),
        )
        conn.commit()
        return room_id

def get_room(room_id):
    with lock, connect() as conn:
        row = conn.execute("SELECT * FROM rooms WHERE id = ?", (room_id,)).fetchone()
        return dict(row) if row else None

def list_rooms(kind=None, only_open=True):
    with lock, connect() as conn:
        q = "SELECT * FROM rooms"
        where = []
        params = []
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if only_open:
            where.append("is_open = 1")
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY id DESC"
        rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

def room_member_count(room_id):
    with lock, connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM room_members WHERE room_id = ?", (room_id,)).fetchone()
        return int(row["c"])

def is_member(room_id, user_id):
    with lock, connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM room_members WHERE room_id = ? AND user_id = ?",
            (room_id, user_id),
        ).fetchone()
        return row is not None

def join_room(room_id, user_id):
    with lock, connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO room_members(room_id, user_id, joined_at) VALUES (?, ?, ?)",
            (room_id, user_id, now_iso()),
        )
        conn.execute("UPDATE users SET active_room_id = ? WHERE user_id = ?", (room_id, user_id))
        conn.commit()

def leave_room(room_id, user_id):
    with lock, connect() as conn:
        conn.execute("DELETE FROM room_members WHERE room_id = ? AND user_id = ?", (room_id, user_id))
        conn.execute(
            "UPDATE users SET active_room_id = NULL WHERE user_id = ? AND active_room_id = ?",
            (user_id, room_id),
        )
        conn.commit()

def get_room_members(room_id):
    with lock, connect() as conn:
        rows = conn.execute("""
            SELECT u.user_id, u.username, u.first_name, u.plan
            FROM room_members rm
            JOIN users u ON u.user_id = rm.user_id
            WHERE rm.room_id = ?
            ORDER BY rm.joined_at ASC
        """, (room_id,)).fetchall()
        return [dict(r) for r in rows]

def save_message(room_id, user_id, kind, content):
    with lock, connect() as conn:
        conn.execute(
            "INSERT INTO messages(room_id, user_id, kind, content, created_at) VALUES (?, ?, ?, ?, ?)",
            (room_id, user_id, kind, content, now_iso()),
        )
        conn.commit()

def format_user_label(user_row):
    if not user_row:
        return "unknown"
    if user_row.get("plan") == "VIP" or user_row.get("plan") == "BUSINESS":
        return f"★ {user_row.get('first_name') or user_row.get('username') or user_row['user_id']}"
    return f"@{user_row.get('username')}" if user_row.get("username") else (user_row.get("first_name") or str(user_row["user_id"]))

def room_title(room):
    return f"#{room['id']} {room['name']}"

def room_emoji(room):
    return "🔒" if room["kind"] == "vip" else "🌍"

def user_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🎉 Комнаты", callback_data="menu:rooms"),
        types.InlineKeyboardButton("👤 Профиль", callback_data="menu:profile"),
    )
    kb.add(
        types.InlineKeyboardButton("💎 Подписки", callback_data="menu:plans"),
        types.InlineKeyboardButton("🎮 Игры", callback_data="menu:games"),
    )
    kb.add(
        types.InlineKeyboardButton("🛟 Помощь", callback_data="menu:help"),
    )
    return kb

def rooms_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➕ Создать", callback_data="room:create"),
        types.InlineKeyboardButton("📋 Список", callback_data="room:list"),
    )
    kb.add(
        types.InlineKeyboardButton("🏠 Моя комната", callback_data="room:mine"),
        types.InlineKeyboardButton("🔙 Назад", callback_data="menu:home"),
    )
    return kb

def plans_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("FREE", callback_data="plan:FREE"),
        types.InlineKeyboardButton("NORM", callback_data="plan:NORM"),
        types.InlineKeyboardButton("VIP", callback_data="plan:VIP"),
        types.InlineKeyboardButton("BUSINESS", callback_data="plan:BUSINESS"),
    )
    kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="menu:home"))
    return kb

def back_to_rooms():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🔙 К комнатам", callback_data="menu:rooms"))
    return kb

def room_actions_menu(room_id, is_owner=False):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("➡️ Войти", callback_data=f"room:join:{room_id}"),
        types.InlineKeyboardButton("👥 Участники", callback_data=f"room:members:{room_id}"),
    )
    kb.add(
        types.InlineKeyboardButton("⭐ Сделать активной", callback_data=f"room:activate:{room_id}"),
        types.InlineKeyboardButton("🚪 Выйти", callback_data=f"room:leave:{room_id}"),
    )
    if is_owner:
        kb.add(
            types.InlineKeyboardButton("🔐 Закрыть", callback_data=f"room:close:{room_id}"),
            types.InlineKeyboardButton("📣 Оповестить", callback_data=f"room:announce:{room_id}"),
        )
    kb.add(types.InlineKeyboardButton("🔙 К списку", callback_data="room:list"))
    return kb

def admin_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🧪 Выдать VIP (тест)", callback_data="admin:vip_test"),
        types.InlineKeyboardButton("🧪 Выдать NORM (тест)", callback_data="admin:norm_test"),
    )
    kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="menu:home"))
    return kb

def render_plan_card(plan_key):
    plan = PLAN_CONFIG[plan_key]
    lines = [
        f"💎 <b>{plan['display_name']}</b>",
        "",
        f"• Ник виден всем: {'да' if plan['visible_nick'] else 'нет'}",
        f"• Анонимный ник: {'да' if plan['anonymous_nick'] else 'нет'}",
        f"• Задержка сообщений: {plan['message_delay_ms']} мс",
        f"• Комнаты: {'без лимита' if plan['unlimited_rooms'] else f'лимит зависит от правил'}",
        f"• Комнаты до: {plan['public_room_max']} человек (обычные)",
        f"• VIP-комнаты до: {plan['vip_room_max']} человек",
        f"• Медиа: {'да' if plan['can_send_media'] else 'нет'}",
        f"• Другие сервера: {'да' if plan['can_join_external_servers'] else 'нет'}",
    ]
    return "\n".join(lines)

def plan_for_room_creation(user_plan, kind):
    cfg = PLAN_CONFIG[user_plan]
    if kind == "vip":
        max_members = cfg["vip_room_max"]
    else:
        max_members = cfg["public_room_max"]
    return max_members

def allowed_create_count(user_plan):
    cfg = PLAN_CONFIG[user_plan]
    return cfg["room_create_weekly"]

def can_send_media(plan_key):
    return PLAN_CONFIG[plan_key]["can_send_media"]

def current_active_room(user_id):
    user = get_user_by_id(user_id)
    if not user or not user["active_room_id"]:
        return None
    return get_room(int(user["active_room_id"]))

def broadcast_room_message(room_id, sender_id, kind, content, media_kind=None, media_file_id=None, caption=None):
    members = get_room_members(room_id)
    sender = get_user_by_id(sender_id)
    room = get_room(room_id)
    label = format_user_label(sender)
    header = f"🏠 <b>{room_emoji(room)} {room_title(room)}</b>\n👤 {label}\n"

    for m in members:
        if m["user_id"] == sender_id:
            continue
        try:
            if kind == "text":
                bot.send_message(m["user_id"], header + f"\n{content}")
            elif kind == "media" and media_kind == "photo":
                bot.send_photo(m["user_id"], media_file_id, caption=header + (caption or ""))
            elif kind == "media" and media_kind == "document":
                bot.send_document(m["user_id"], media_file_id, caption=header + (caption or ""))
            elif kind == "media" and media_kind == "video":
                bot.send_video(m["user_id"], media_file_id, caption=header + (caption or ""))
            elif kind == "media" and media_kind == "voice":
                bot.send_voice(m["user_id"], media_file_id, caption=header + (caption or ""))
            elif kind == "media" and media_kind == "audio":
                bot.send_audio(m["user_id"], media_file_id, caption=header + (caption or ""))
            elif kind == "media" and media_kind == "sticker":
                bot.send_sticker(m["user_id"], media_file_id)
                bot.send_message(m["user_id"], header + (caption or ""))
        except Exception:
            # ignore blocked users / deleted chats
            pass

def is_admin(user_id):
    return user_id in ADMIN_IDS

def safe_username(user):
    if user.username:
        return f"@{user.username}"
    return user.first_name or str(user.id)

# ---------------------
# Commands
# ---------------------
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    user = get_or_create_user(msg.from_user)
    set_active_room(msg.from_user.id, None)
    text = (
        "🥳 <b>Party Discord Bot</b>\n\n"
        "Комнаты, роли, чат внутри комнат и меню как у топовых ботов.\n"
        "Нажми кнопку ниже."
    )
    bot.send_message(msg.chat.id, text, reply_markup=user_menu())

@bot.message_handler(commands=["help"])
def cmd_help(msg):
    text = (
        "<b>Команды</b>\n"
        "/start — меню\n"
        "/myid — твой ID\n"
        "/plan — текущая подписка\n"
        "/room — моя активная комната\n"
        "/leave — выйти из активной комнаты\n"
        "/members — участники активной комнаты\n"
        "/poll вопрос|вариант1|вариант2|...\n\n"
        "Сообщения и медиа в личке боту отправляются в активную комнату."
    )
    bot.send_message(msg.chat.id, text, reply_markup=user_menu())

@bot.message_handler(commands=["myid"])
def cmd_myid(msg):
    get_or_create_user(msg.from_user)
    bot.send_message(msg.chat.id, f"🆔 Твой ID: <code>{msg.from_user.id}</code>")

@bot.message_handler(commands=["plan"])
def cmd_plan(msg):
    user = get_or_create_user(msg.from_user)
    plan_key = user["plan"]
    bot.send_message(msg.chat.id, render_plan_card(plan_key), reply_markup=plans_menu())

@bot.message_handler(commands=["room"])
def cmd_room(msg):
    user = get_or_create_user(msg.from_user)
    room = current_active_room(msg.from_user.id)
    if not room:
        bot.send_message(msg.chat.id, "У тебя нет активной комнаты.", reply_markup=rooms_menu())
        return
    members = room_member_count(room["id"])
    bot.send_message(
        msg.chat.id,
        f"🏠 <b>{room_emoji(room)} {room_title(room)}</b>\n"
        f"👥 {members}/{room['max_members']}\n"
        f"Тип: {room['kind']}\n"
        f"Статус: {'открыта' if room['is_open'] else 'закрыта'}",
        reply_markup=room_actions_menu(room["id"], is_owner=(room["owner_id"] == msg.from_user.id))
    )

@bot.message_handler(commands=["leave"])
def cmd_leave(msg):
    user = get_or_create_user(msg.from_user)
    room = current_active_room(msg.from_user.id)
    if not room:
        bot.send_message(msg.chat.id, "Ты ни в какой комнате сейчас не сидишь.")
        return
    leave_room(room["id"], msg.from_user.id)
    bot.send_message(msg.chat.id, f"🚪 Ты вышел из комнаты #{room['id']}", reply_markup=user_menu())

@bot.message_handler(commands=["members"])
def cmd_members(msg):
    room = current_active_room(msg.from_user.id)
    if not room:
        bot.send_message(msg.chat.id, "Сначала зайди в комнату.")
        return
    members = get_room_members(room["id"])
    lines = [f"👥 <b>Участники комнаты #{room['id']}</b>"]
    for m in members:
        lines.append(f"• {format_user_label(m)}")
    bot.send_message(msg.chat.id, "\n".join(lines))

@bot.message_handler(commands=["poll"])
def cmd_poll(msg):
    room = current_active_room(msg.from_user.id)
    if not room:
        bot.send_message(msg.chat.id, "Сначала выбери активную комнату.")
        return
    payload = msg.text.partition(" ")[2].strip()
    parts = [p.strip() for p in payload.split("|") if p.strip()]
    if len(parts) < 3:
        bot.send_message(msg.chat.id, "Формат: /poll вопрос|вариант1|вариант2|...")
        return
    question = parts[0]
    options = parts[1:11]
    try:
        sent = bot.send_poll(msg.chat.id, question, options, is_anonymous=False)
    except Exception as e:
        bot.send_message(msg.chat.id, f"Не смог создать опрос: {e}")
        return
    save_message(room["id"], msg.from_user.id, "poll", question)

@bot.message_handler(commands=["grant"])
def cmd_grant(msg):
    if not is_admin(msg.from_user.id):
        bot.send_message(msg.chat.id, "Команда только для админа.")
        return
    parts = msg.text.split()
    if len(parts) != 3 or not parts[1].isdigit():
        bot.send_message(msg.chat.id, "Формат: /grant USER_ID PLAN")
        return
    user_id = int(parts[1])
    plan = parts[2].upper()
    if plan not in PLAN_CONFIG:
        bot.send_message(msg.chat.id, "PLAN: FREE, NORM, VIP, BUSINESS")
        return
    if not get_user_by_id(user_id):
        bot.send_message(msg.chat.id, "Пользователь не найден в базе. Он должен сначала написать боту /start")
        return
    set_user_plan(user_id, plan)
    bot.send_message(msg.chat.id, f"Готово: {user_id} → {plan}")

# ---------------------
# Menu callbacks
# ---------------------
@bot.callback_query_handler(func=lambda c: True)
def on_callback(call):
    try:
        get_or_create_user(call.from_user)
        data = call.data or ""
        user = get_user_by_id(call.from_user.id)
        plan = user["plan"]

        if data == "menu:home":
            bot.edit_message_text(
                "🏠 <b>Главное меню</b>\nВыбирай раздел:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=user_menu()
            )

        elif data == "menu:rooms":
            bot.edit_message_text(
                "🎉 <b>Комнаты</b>\nСоздавай, заходи и общайся как в мини-Discord.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=rooms_menu()
            )

        elif data == "menu:plans":
            text = "\n\n".join(render_plan_card(k) for k in ["FREE", "NORM", "VIP", "BUSINESS"])
            bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=plans_menu()
            )

        elif data == "menu:profile":
            active = current_active_room(call.from_user.id)
            text = (
                f"👤 <b>Профиль</b>\n"
                f"• Ник: {safe_username(call.from_user)}\n"
                f"• План: <b>{plan}</b>\n"
                f"• Активная комната: #{active['id']} {active['name']}" if active else "None"
            )
            if active:
                text = (
                    f"👤 <b>Профиль</b>\n"
                    f"• Ник: {safe_username(call.from_user)}\n"
                    f"• План: <b>{plan}</b>\n"
                    f"• Активная комната: #{active['id']} {active['name']}"
                )
            else:
                text = (
                    f"👤 <b>Профиль</b>\n"
                    f"• Ник: {safe_username(call.from_user)}\n"
                    f"• План: <b>{plan}</b>\n"
                    f"• Активная комната: нет"
                )
            bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("🔙 Назад", callback_data="menu:home")
                )
            )

        elif data == "menu:games":
            text = (
                "🎮 <b>Игры</b>\n"
                "Сейчас доступны команды комнаты:\n"
                "• /poll — опрос\n"
                "• чат комнаты — обычные сообщения\n\n"
                "Позже можно добавить мини-игры."
            )
            bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("🔙 Назад", callback_data="menu:home")
                )
            )

        elif data == "menu:help":
            bot.edit_message_text(
                "Помощь:\n\nПиши /help или открой разделы комнат и профиля.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=types.InlineKeyboardMarkup().add(
                    types.InlineKeyboardButton("🔙 Назад", callback_data="menu:home")
                )
            )

        elif data == "room:create":
            created = get_rooms_created_this_week(call.from_user.id)
            limit = allowed_create_count(plan)
            if limit is not None and created >= limit:
                bot.answer_callback_query(call.id, f"Лимит создания комнат на неделе: {limit}")
                return

            kind = "vip" if plan in ("VIP", "BUSINESS") else "public"
            room_name = f"Комната {datetime.utcnow().strftime('%H%M%S')}"
            max_members = plan_for_room_creation(plan, kind)
            room_id = create_room(call.from_user.id, room_name, kind, max_members)
            incr_rooms_created(call.from_user.id)
            set_active_room(call.from_user.id, room_id)
            bot.edit_message_text(
                f"✅ <b>Комната создана</b>\n"
                f"ID: #{room_id}\n"
                f"Название: {room_name}\n"
                f"Тип: {kind}\n"
                f"Лимит: {max_members}\n\n"
                f"Теперь все сообщения в личке боту уйдут в эту комнату.",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=room_actions_menu(room_id, is_owner=True)
            )

        elif data == "room:list":
            rooms = list_rooms()
            if not rooms:
                bot.edit_message_text(
                    "Комнат пока нет.",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=rooms_menu()
                )
                return

            lines = ["📋 <b>Список комнат</b>"]
            kb = types.InlineKeyboardMarkup(row_width=1)
            for r in rooms[:30]:
                count = room_member_count(r["id"])
                lines.append(
                    f"{room_emoji(r)} #{r['id']} {r['name']} • {count}/{r['max_members']} • {r['kind']}"
                )
                kb.add(types.InlineKeyboardButton(
                    f"Войти в #{r['id']} {r['name']}",
                    callback_data=f"room:join:{r['id']}"
                ))
            kb.add(types.InlineKeyboardButton("🔙 Назад", callback_data="menu:rooms"))
            bot.edit_message_text("\n".join(lines), call.message.chat.id, call.message.message_id, reply_markup=kb)

        elif data == "room:mine":
            room = current_active_room(call.from_user.id)
            if not room:
                bot.answer_callback_query(call.id, "Активной комнаты нет")
                return
            count = room_member_count(room["id"])
            bot.edit_message_text(
                f"🏠 <b>{room_emoji(room)} {room_title(room)}</b>\n"
                f"👥 {count}/{room['max_members']}\n"
                f"Тип: {room['kind']}\n"
                f"Открыта: {'да' if room['is_open'] else 'нет'}",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=room_actions_menu(room["id"], is_owner=(room["owner_id"] == call.from_user.id))
            )

        elif data.startswith("room:join:"):
            room_id = int(data.split(":")[-1])
            room = get_room(room_id)
            if not room:
                bot.answer_callback_query(call.id, "Комната не найдена")
                return
            if not room["is_open"]:
                bot.answer_callback_query(call.id, "Комната закрыта")
                return
            if is_member(room_id, call.from_user.id):
                set_active_room(call.from_user.id, room_id)
                bot.answer_callback_query(call.id, "Уже в комнате")
                bot.send_message(call.message.chat.id, f"Ты уже в #{room_id}. Комната активирована.")
                return
            if room_member_count(room_id) >= room["max_members"]:
                bot.answer_callback_query(call.id, "Комната заполнена")
                return

            join_room(room_id, call.from_user.id)
            bot.answer_callback_query(call.id, "Вошёл в комнату")
            bot.send_message(
                call.message.chat.id,
                f"✅ Ты вошёл в <b>#{room_id} {room['name']}</b>\n"
                f"Пиши сообщения в личке боту — они уйдут в комнату.",
                reply_markup=room_actions_menu(room_id, is_owner=(room["owner_id"] == call.from_user.id))
            )

        elif data.startswith("room:leave:"):
            room_id = int(data.split(":")[-1])
            if not is_member(room_id, call.from_user.id):
                bot.answer_callback_query(call.id, "Ты не в этой комнате")
                return
            leave_room(room_id, call.from_user.id)
            bot.answer_callback_query(call.id, "Вышел")
            bot.send_message(call.message.chat.id, f"🚪 Ты вышел из #{room_id}", reply_markup=rooms_menu())

        elif data.startswith("room:activate:"):
            room_id = int(data.split(":")[-1])
            if not is_member(room_id, call.from_user.id):
                bot.answer_callback_query(call.id, "Сначала войди в комнату")
                return
            set_active_room(call.from_user.id, room_id)
            bot.answer_callback_query(call.id, "Активная комната выбрана")
            bot.send_message(call.message.chat.id, f"⭐ Активная комната: #{room_id}")

        elif data.startswith("room:members:"):
            room_id = int(data.split(":")[-1])
            if not get_room(room_id):
                bot.answer_callback_query(call.id, "Комната не найдена")
                return
            members = get_room_members(room_id)
            lines = [f"👥 <b>Участники #{room_id}</b>"]
            for m in members:
                lines.append(f"• {format_user_label(m)}")
            bot.edit_message_text(
                "\n".join(lines),
                call.message.chat.id,
                call.message.message_id,
                reply_markup=room_actions_menu(room_id, is_owner=False)
            )

        elif data.startswith("room:close:"):
            room_id = int(data.split(":")[-1])
            room = get_room(room_id)
            if not room or room["owner_id"] != call.from_user.id:
                bot.answer_callback_query(call.id, "Только владелец")
                return
            with lock, connect() as conn:
                conn.execute("UPDATE rooms SET is_open = 0 WHERE id = ?", (room_id,))
                conn.commit()
            bot.answer_callback_query(call.id, "Комната закрыта")
            bot.send_message(call.message.chat.id, f"🔐 Комната #{room_id} закрыта.")

        elif data.startswith("room:announce:"):
            room_id = int(data.split(":")[-1])
            room = get_room(room_id)
            if not room or room["owner_id"] != call.from_user.id:
                bot.answer_callback_query(call.id, "Только владелец")
                return
            bot.send_message(
                call.message.chat.id,
                "Напиши текст следующим сообщением — он уйдёт как объявление в твою комнату."
            )
            with lock, connect() as conn:
                conn.execute("UPDATE users SET active_room_id = ? WHERE user_id = ?", (room_id, call.from_user.id))
                conn.commit()
            bot.answer_callback_query(call.id, "Жду сообщение")

        elif data.startswith("plan:"):
            target_plan = data.split(":", 1)[1]
            if target_plan not in PLAN_CONFIG:
                bot.answer_callback_query(call.id, "Unknown plan")
                return
            text = render_plan_card(target_plan)
            if is_admin(call.from_user.id):
                text += "\n\n🧪 Админ режим: можешь выдать план через /grant USER_ID PLAN"
            bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=plans_menu()
            )

        elif data == "admin:vip_test":
            if not is_admin(call.from_user.id):
                bot.answer_callback_query(call.id, "Только админ")
                return
            set_user_plan(call.from_user.id, "VIP")
            bot.answer_callback_query(call.id, "VIP включён для тебя")
            bot.send_message(call.message.chat.id, "Тестовый VIP включён. /plan чтобы посмотреть.")

        elif data == "admin:norm_test":
            if not is_admin(call.from_user.id):
                bot.answer_callback_query(call.id, "Только админ")
                return
            set_user_plan(call.from_user.id, "NORM")
            bot.answer_callback_query(call.id, "NORM включён для тебя")
            bot.send_message(call.message.chat.id, "Тестовый NORM включён. /plan чтобы посмотреть.")

        else:
            bot.answer_callback_query(call.id, "Неизвестное действие")

    except Exception as e:
        try:
            bot.answer_callback_query(call.id, "Ошибка")
        except Exception:
            pass
        try:
            bot.send_message(call.message.chat.id, f"Ошибка: {e}")
        except Exception:
            pass

# ---------------------
# Message relay
# ---------------------
def rate_limit_check(user_id, plan_key):
    cfg = PLAN_CONFIG[plan_key]
    usage = today_usage(user_id)
    # Text delay by message count, approximate simple limit
    # We avoid hard blocking too aggressively; just count messages.
    if cfg["message_delay_ms"] > 0:
        # soft delay: enforce max message pace by text count to keep it working on DM bot
        pass
    return True, ""

def get_voice_daily_limit_hours(plan_key, room_kind):
    cfg = PLAN_CONFIG[plan_key]
    if room_kind == "vip":
        return cfg["vip_voice_daily_hours"]
    return cfg["public_voice_daily_hours"]

def room_can_accept_message(user, room):
    plan_key = user["plan"]
    cfg = PLAN_CONFIG[plan_key]
    if room["kind"] == "vip" and plan_key not in ("VIP", "BUSINESS"):
        return False, "Эта комната только для VIP/Business."
    if room["kind"] == "vip" and room["max_members"] > cfg["vip_room_max"] and plan_key != "BUSINESS":
        # strict check for entering bigger room than allowed; joining handles it already
        return False, "Слишком большой лимит комнаты для твоего плана."
    return True, ""

def send_room_text(msg, content):
    room = current_active_room(msg.from_user.id)
    if not room:
        return False
    user = get_or_create_user(msg.from_user)
    ok, reason = room_can_accept_message(user, room)
    if not ok:
        bot.reply_to(msg, f"⛔ {reason}")
        return True

    add_text_usage(msg.from_user.id, 1)
    save_message(room["id"], msg.from_user.id, "text", content)
    broadcast_room_message(room["id"], msg.from_user.id, "text", content)
    incr_popularity(msg.from_user.id, 1)
    return True

@bot.message_handler(content_types=["text", "photo", "document", "video", "audio", "voice", "sticker"])
def on_any_message(msg):
    if msg.chat.type != "private":
        return

    text = (msg.text or "").strip()

    # avoid intercepting commands
    if text.startswith("/"):
        return

    user = get_or_create_user(msg.from_user)
    room = current_active_room(msg.from_user.id)

    # if no active room, treat as chat with bot and guide user
    if not room:
        if msg.content_type == "text":
            bot.send_message(
                msg.chat.id,
                "У тебя нет активной комнаты.\n"
                "Открой /start → Комнаты → Создать или выбери из списка."
            )
        else:
            bot.send_message(msg.chat.id, "Сначала зайди в комнату, потом отправляй медиа.")
        return

    ok, reason = room_can_accept_message(user, room)
    if not ok:
        bot.send_message(msg.chat.id, f"⛔ {reason}")
        return

    plan_key = user["plan"]
    cfg = PLAN_CONFIG[plan_key]

    if msg.content_type == "text":
        if not text:
            return
        add_text_usage(msg.from_user.id, 1)
        if cfg["message_delay_ms"] > 0:
            time.sleep(cfg["message_delay_ms"] / 1000.0)
        save_message(room["id"], msg.from_user.id, "text", text)
        broadcast_room_message(room["id"], msg.from_user.id, "text", text)
        incr_popularity(msg.from_user.id, 1)

    elif msg.content_type == "photo":
        if not cfg["can_send_media"]:
            bot.send_message(msg.chat.id, "Медиа доступно начиная с NORM.")
            return
        file_id = msg.photo[-1].file_id
        caption = msg.caption or ""
        save_message(room["id"], msg.from_user.id, "media", f"photo:{file_id}")
        broadcast_room_message(room["id"], msg.from_user.id, "media", "photo", media_kind="photo", media_file_id=file_id, caption=caption)

    elif msg.content_type == "document":
        if not cfg["can_send_media"]:
            bot.send_message(msg.chat.id, "Медиа доступно начиная с NORM.")
            return
        file_id = msg.document.file_id
        caption = msg.caption or msg.document.file_name or ""
        save_message(room["id"], msg.from_user.id, "media", f"document:{file_id}")
        broadcast_room_message(room["id"], msg.from_user.id, "media", "document", media_kind="document", media_file_id=file_id, caption=caption)

    elif msg.content_type == "video":
        if not cfg["can_send_media"]:
            bot.send_message(msg.chat.id, "Медиа доступно начиная с NORM.")
            return
        file_id = msg.video.file_id
        caption = msg.caption or ""
        save_message(room["id"], msg.from_user.id, "media", f"video:{file_id}")
        broadcast_room_message(room["id"], msg.from_user.id, "media", "video", media_kind="video", media_file_id=file_id, caption=caption)

    elif msg.content_type == "audio":
        if not cfg["can_send_media"]:
            bot.send_message(msg.chat.id, "Медиа доступно начиная с NORM.")
            return
        file_id = msg.audio.file_id
        caption = msg.caption or msg.audio.title or ""
        save_message(room["id"], msg.from_user.id, "media", f"audio:{file_id}")
        broadcast_room_message(room["id"], msg.from_user.id, "media", "audio", media_kind="audio", media_file_id=file_id, caption=caption)

    elif msg.content_type == "voice":
        if not cfg["can_send_media"]:
            bot.send_message(msg.chat.id, "Голосовые доступны начиная с NORM.")
            return
        file_id = msg.voice.file_id
        secs = getattr(msg.voice, "duration", 0) or 0
        add_voice_usage(msg.from_user.id, secs)
        save_message(room["id"], msg.from_user.id, "media", f"voice:{file_id}")
        broadcast_room_message(room["id"], msg.from_user.id, "media", "voice", media_kind="voice", media_file_id=file_id, caption=f"voice {secs}s")

    elif msg.content_type == "sticker":
        if not cfg["can_send_media"]:
            bot.send_message(msg.chat.id, "Стикеры доступны начиная с NORM.")
            return
        file_id = msg.sticker.file_id
        save_message(room["id"], msg.from_user.id, "media", f"sticker:{file_id}")
        broadcast_room_message(room["id"], msg.from_user.id, "media", "sticker", media_kind="sticker", media_file_id=file_id, caption="")

# ---------------------
# Web
# ---------------------
@app.route("/", methods=["GET"])
def index():
    return (
        "<h1>Party Discord Bot</h1>"
        "<p>Bot is running.</p>"
        "<p>Use Telegram to interact.</p>"
    )

@app.route("/healthz", methods=["GET"])
def healthz():
    return {"ok": True}

@app.route("/", methods=["POST"])
def webhook():
    update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
    bot.process_new_updates([update])
    return "ok"

def run():
    init_db()
    bot.remove_webhook()
    webhook_url = os.getenv("RENDER_EXTERNAL_URL")
    if webhook_url:
        bot.set_webhook(url=f"{webhook_url}/")
    app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    run()
