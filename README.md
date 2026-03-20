# Party Discord Bot

Telegram bot with Discord-like room system, inline menus, user plans, chat relay, media relay, polls, and Render-ready webhook hosting.

## Files
- `main.py`
- `requirements.txt`

## Render setup
- Create a **Web Service**
- Build command: `pip install -r requirements.txt`
- Start command: `python main.py`
- Env vars:
  - `BOT_TOKEN=...`
  - `ADMIN_IDS=123456789` (optional, comma-separated)
  - `PORT=10000` (optional)
  - `DB_PATH=party_bot.sqlite3` (optional)

## How it works
- Open `/start`
- Create or join a room
- Set an active room
- Send text/media in the bot chat
- It relays messages to everyone in the active room

## Plans
Use `/plan` to view the current plan.
Use `/grant USER_ID PLAN` as admin for testing.

Plans: `FREE`, `NORM`, `VIP`, `BUSINESS`
