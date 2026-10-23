"""Engine controls shared by Telegram commands and session-bound panels."""
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from . import antigravity_engine, codex_engine

LABELS = {"claude": "Claude · Anthropic", "codex": "Codex · OpenAI",
          "antigravity": "Antigravity · Google"}
BACKENDS = {"codex": codex_engine, "antigravity": antigravity_engine}


def readiness(engine):
    if engine == "claude":
        return True, ""
    try:
        return BACKENDS[engine].available()
    except Exception:
        return False, "בדיקת הזמינות נכשלה"


def active_model(session):
    backend = BACKENDS.get(session.engine)
    return (backend.get_model(session.skey) if backend else session.cfg.model) or "default"


def select(session, engine):
    """Change only the selected route. Never reset native threads or history."""
    if engine not in LABELS:
        return False, "❌ ספק לא מוכר. בחר מהרשימה."
    if session.cfg.read_only and engine != "claude":
        return False, "🔒 תפקיד לקריאה בלבד דורש מנוע שאוכף את ההרשאות שלו."
    if session.busy:
        return False, "⏳ יש תשובה בתהליך. החלף ספק בסיום התשובה."
    if session.engine == engine:
        return True, "זה הספק הפעיל בשיחה הזו."
    ready, reason = readiness(engine)
    if not ready:
        return False, f"❌ לא ניתן לעבור אל {LABELS[engine]}: {reason}"
    session.engine = engine
    session.handoff.state["engine"] = engine
    session.handoff.save()
    return True, "✅ הספק הוחלף. ההודעה הבאה תמשיך עם הקשר השיחה."


def card(session, notice=""):
    lines = ["🔀 בחירת ספק לשיחה הזו",
             f"פעיל: {LABELS.get(session.engine, session.engine)}",
             f"מודל: {active_model(session)}"]
    if notice:
        lines += ["", notice]
    lines += ["", "הקשר השיחה נשמר במעבר בין ספקים.",
              "בדיקת הזמינות בודקת את ההתקנה והחיבור המקומיים; המכסה נבדקת בזמן הריצה."]
    if session.busy:
        lines.append("⏳ החלפת ספק תתאפשר בסיום התשובה.")
    buttons = []
    for engine, label in LABELS.items():
        ready, reason = readiness(engine)
        marker = "● " if engine == session.engine else ""
        status = "" if ready else " · לא זמין"
        buttons.append([InlineKeyboardButton(marker + label + status,
                        callback_data=f"eng:{session.sid}:{engine}")])
        if not ready:
            lines.append(f"{label}: {reason}")
    buttons += [[InlineKeyboardButton("🧠 בחירת מודל", callback_data=f"eng:{session.sid}:models")],
                [InlineKeyboardButton("↻ רענון", callback_data=f"eng:{session.sid}:menu"),
                 InlineKeyboardButton("⬅ חזרה לפאנל", callback_data=f"eng:{session.sid}:back")]]
    return "\n".join(lines), InlineKeyboardMarkup(buttons)


def bind_models(markup, sid):
    """Keep a model picker opened from an old panel on its original session."""
    rows = []
    for row in markup.inline_keyboard:
        buttons = []
        for button in row:
            data = button.callback_data or ""
            if data.startswith(("model:", "cmodel:", "amodel:")):
                data = f"eng:{sid}:pick:{data}"
            elif data == "menu:model":
                data = f"eng:{sid}:models"
            elif data == "menu:back":
                data = f"eng:{sid}:menu"
            elif data == "menu:xmodel":
                # Paid one-shot providers have their own /models flow.
                continue
            if len(data.encode()) <= 64:
                buttons.append(InlineKeyboardButton(button.text, callback_data=data))
        if buttons:
            rows.append(buttons)
    return InlineKeyboardMarkup(rows)
