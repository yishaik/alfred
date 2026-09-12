"""Telegram controls for the direct LG webOS connection.

Purpose:  Expose /tv and inline remote buttons without an agent session.
Inputs:   Authorized Telegram commands and tv: callbacks.
Outputs:  Hebrew remote panel, settings, and direct LAN commands.
Key fns:  cmd_tv, on_tv_callback, legacy_tap, shutdown.
Deps:     telegram, tv, config.authorized_chat.
Note:     Callback data has a separate namespace; ordinary OK text stays chat.
Updated:  2026-09-12
"""

import hashlib

from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup
from telegram.error import BadRequest

from .config import authorized_chat
from .tv import LGRemote, TVError

HELP = (
    "📺 שלט LG webOS\n"
    "הפעל את הטלוויזיה וחבר אותה לרשת של המחשב שמריץ את הברידג׳.\n"
    "חיבור ראשוני: /tv connect 192.168.1.50\n"
    "אשר את בקשת הצימוד על מסך הטלוויזיה.\n\n"
    "/tv — פתיחת השלט\n/tv status — מצב החיבור\n"
    "/tv pair — צימוד מחדש\n/tv vol 12 — עוצמת קול\n"
    "/tv mute או /tv unmute — השתקה וביטולה\n"
    "/tv apps — אפליקציות\n/tv inputs — כניסות HDMI\n"
    "/tv text טקסט — הקלדה בשדה פעיל\n"
    "/tv on או /tv off — הדלקה וכיבוי\n"
    "/tv mac AA:BB:CC:DD:EE:FF — הגדרת הדלקה דרך הרשת\n"
    "אפשר להוסיף אחרי ה־MAC כתובת broadcast מקומית."
)

# Older reply keyboards remain useful except the generic "OK", which must
# remain normal conversation. The new remote exclusively uses callbacks.
LEGACY = {
    "⬆️": ("button", "UP"), "⬇️": ("button", "DOWN"),
    "⬅️": ("button", "LEFT"), "➡️": ("button", "RIGHT"),
    "↩️ Back": ("button", "BACK"), "🏠": ("button", "HOME"),
    "⏸️": ("pause", None), "▶️": ("play", None),
    "🔊 +": ("volup", None), "🔉 −": ("voldown", None),
    "🔇": ("toggle_mute", None), "📺 מצב": ("status", None),
}


def get_remote(ctx):
    data = ctx.application.bot_data
    if "tv_remote" not in data:
        data["tv_remote"] = LGRemote()
    return data["tv_remote"]


def remote_keyboard():
    def b(label, action):
        return Button(label, callback_data="tv:" + action)
    return InlineKeyboardMarkup([
        [b("🔌 הדלקה", "on"), b("⏻ כיבוי", "off")],
        [b("🏠 בית", "button:HOME"), b("⬆️", "button:UP"), b("↩️ חזרה", "button:BACK")],
        [b("⬅️", "button:LEFT"), b("OK", "button:ENTER"), b("➡️", "button:RIGHT")],
        [b("☰ תפריט", "button:MENU"), b("⬇️", "button:DOWN"), b("יציאה", "button:EXIT")],
        [b("🔉 −", "voldown"), b("🔇 השתקה / ביטול", "toggle_mute"), b("🔊 +", "volup")],
        [b("▶️ ניגון", "play"), b("⏸ השהיה", "pause")],
        [b("ערוץ −", "channel_down"), b("ערוץ +", "channel_up")],
        [b("אפליקציות", "apps:0"), b("כניסות HDMI", "inputs:0")],
        [b("🔄 מצב", "status"), b("❔ עזרה", "help")],
    ])


async def _show(update, text, keyboard=None):
    keyboard = keyboard or remote_keyboard()
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard)
        except BadRequest as exc:
            if "message is not modified" not in str(exc).lower():
                raise
    else:
        await update.effective_message.reply_text(text, reply_markup=keyboard)


def _token(identifier):
    return hashlib.sha256(identifier.encode()).hexdigest()[:16]


async def _catalog(update, remote, kind, argument="0"):
    items = await remote.execute(kind)
    items = [item for item in items if isinstance(item, dict) and isinstance(item.get("id"), str)]
    if argument.startswith("select-"):
        token = argument.removeprefix("select-")
        item = next((item for item in items if _token(item["id"]) == token), None)
        if item is None:
            raise TVError("הבחירה כבר לא זמינה. פתח שוב את רשימת האפליקציות או הכניסות.")
        result = await remote.execute("app" if kind == "apps" else "input", item["id"])
        await _show(update, "📺 " + result)
        return
    if not argument.isdigit() or len(argument) > 5:
        raise TVError("עמוד לא תקין. פתח שוב /tv.")
    start = min(int(argument), max(0, ((len(items) - 1) // 8) * 8))
    page = items[start:start + 8]
    rows = [[Button(str(item.get("title") or item.get("label") or item["id"])[:60],
                    callback_data=f"tv:{kind}:select-{_token(item['id'])}")] for item in page]
    nav = []
    if start:
        nav.append(Button("הקודם", callback_data=f"tv:{kind}:{max(0, start - 8)}"))
    if start + 8 < len(items):
        nav.append(Button("הבא", callback_data=f"tv:{kind}:{start + 8}"))
    if nav:
        rows.append(nav)
    rows.append([Button("↩️ לשלט", callback_data="tv:panel")])
    title = "אפליקציות" if kind == "apps" else "כניסות HDMI"
    text = f"📺 {title} · {start + 1}–{start + len(page)} מתוך {len(items)}" if items else f"📺 לא נמצאו {title}"
    await _show(update, text, InlineKeyboardMarkup(rows))


async def cmd_tv(update, ctx):
    if not authorized_chat(update.effective_chat.id) or update.effective_user.is_bot:
        return
    args = list(ctx.args or [])
    action = args.pop(0).lower() if args else "panel"
    remote = get_remote(ctx)
    try:
        if action == "help" or (action == "panel" and not remote.host):
            await _show(update, HELP)
            return
        if action in {"connect", "pair"}:
            if len(args) > 1 or (not args and not remote.host):
                raise TVError("שימוש: /tv connect 192.168.1.50")
            await remote.configure(args[0] if args else remote.host, pair=action == "pair")
            if not remote.paired:
                await update.effective_message.reply_text("📺 מתחבר. אם מופיעה בקשת צימוד על מסך הטלוויזיה, אשר אותה עכשיו.")
            await _show(update, "📺 " + await remote.execute("status"))
            return
        if action == "mac":
            if not 1 <= len(args) <= 2:
                raise TVError("שימוש: /tv mac AA:BB:CC:DD:EE:FF [broadcast]")
            await remote.configure_wake(*args)
            await _show(update, "📺 כתובת MAC נשמרה. הפעל בטלוויזיה הדלקה דרך הרשת כדי להשתמש בכפתור הדלקה.")
            return
        if action in {"apps", "inputs"}:
            await _catalog(update, remote, action)
            return
        value = None
        if action in {"vol", "volume"}:
            if len(args) != 1 or not args[0].isdigit() or len(args[0]) > 3:
                raise TVError("שימוש: /tv vol 12 — מספר שלם בין 0 ל־100.")
            action, value = "volume", int(args[0])
        elif action == "text":
            value = " ".join(args)
        elif action in {"button", "app", "input"}:
            if len(args) != 1:
                raise TVError(f"שימוש: /tv {action} מזהה")
            value = args[0].upper() if action == "button" else args[0]
        elif args:
            raise TVError("פרמטרים לא תקינים. השתמש ב־/tv help.")
        result = await remote.execute("status" if action == "panel" else action, value)
        await _show(update, "📺 " + result)
    except TVError as exc:
        await _show(update, "⚠️ " + str(exc))


async def on_tv_callback(update, ctx):
    query = update.callback_query
    if not authorized_chat(update.effective_chat.id) or query.from_user.is_bot:
        await query.answer("אין הרשאה", show_alert=True)
        return
    await query.answer()
    remote = get_remote(ctx)
    parts = (query.data or "").split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    argument = parts[2] if len(parts) > 2 else None
    try:
        if action == "help":
            await _show(update, HELP)
        elif action == "panel":
            await _show(update, "📺 שלט LG webOS")
        elif action in {"apps", "inputs"}:
            await _catalog(update, remote, action, argument or "0")
        else:
            result = await remote.execute(action, argument)
            await _show(update, "📺 " + result)
    except TVError as exc:
        await _show(update, "⚠️ " + str(exc))


async def legacy_tap(update, ctx):
    if not authorized_chat(update.effective_chat.id) or update.effective_user.is_bot:
        return
    try:
        result = await get_remote(ctx).execute(*LEGACY[update.message.text])
        await _show(update, "📺 " + result)
    except TVError as exc:
        await _show(update, "⚠️ " + str(exc))


async def shutdown(app):
    remote = app.bot_data.get("tv_remote")
    if remote is not None:
        await remote.close()
