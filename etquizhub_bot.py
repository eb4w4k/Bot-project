"""
ETquizhub_bot - Practice + Live Telegram Bot
=========================================================

WHAT THIS BOT DOES
1. /start  -> name -> name on the paying account -> Tier (A = Grade 9-10, B = Grade 11-12)
             -> package (Practice / Live / Bundle) -> payment instructions
2. Student pays outside the bot, then sends /paid -> you (admin) get a notification
3. You check your Telebirr/bank history and run:  /confirm <telegram_id>
   - Practice: unlocks /quiz for their tier
   - Live:     generates their ticket, e.g. ETQL-B-001
   - Bundle:   both, with one payment and one /confirm
4. PRACTICE: /quiz runs today's subject (see SCHEDULE) - 25 questions for the student's tier,
   one message that updates in place, personal timer, limited attempts per day.
5. /leaderboard (admin) shows the top scorers per tier for the day.

===========================================================================
THE THREE SWITCHES (edit them below, then push to GitHub / redeploy)
===========================================================================
BOT_ACTIVE              False -> the WHOLE bot answers every student with the "under update"
                        message. Your admin commands still work.
LIVE_REGISTRATION_OPEN  False -> Live and Bundle are hidden, Practice only (current setting).
                        True  -> Live and Bundle appear when students pick a package.
REGISTRATION_PAUSED     True  -> nobody can register or buy a package (use in weeks with no
                        sessions). Students who already paid can still use /quiz.

===========================================================================
SETUP (Render)
===========================================================================
Environment variables:  BOT_TOKEN, ADMIN_ID, DATABASE_URL (your Neon connection string)
requirements.txt:       pyTelegramBotAPI, flask, psycopg2-binary
Files that must sit next to this one:  questions.py  (made by pick_questions.py each week)
The tables are created automatically the first time the bot starts.

===========================================================================
TIME
===========================================================================
Ethiopia is fixed UTC+3 (no daylight saving). QUIZ_CUTOFF_ETHIOPIAN_HOUR is written in
Ethiopian time-telling (12 = end of the Ethiopian day-time = 18:00 on a normal clock);
the code adds the 6 hours itself. The cutoff only blocks NEW starts - a student who
already started keeps their full time limit.
"""

import html
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, time as dtime

import psycopg2
import psycopg2.extras
import telebot
from flask import Flask
from telebot import types
from telebot.apihelper import ApiTelegramException

from questions import QUESTION_SETS

# ===========================================================================
# CONFIGURATION
# ===========================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
DATABASE_URL = os.environ.get("DATABASE_URL")

# ---- THE THREE SWITCHES -------------------------------------------------
BOT_ACTIVE = True                 # False = whole system shows the "under update" message
LIVE_REGISTRATION_OPEN = False    # False = Practice only; True = Live + Bundle also offered
REGISTRATION_PAUSED = False       # True = no new registrations / package purchases
# -------------------------------------------------------------------------

# Prices in ETB
PRICES = {"practice": 100, "live": 400, "both": 430}
PACKAGE_NAMES = {
    "practice": "Practice package",
    "live": "Live exam",
    "both": "Bundle (Practice + Live)",
}

TIERS = {"A": "Tier A (Grade 9-10)", "B": "Tier B (Grade 11-12)"}

# Weekday -> subject key (Monday=0 ... Sunday=6). None = no session that day.
# Subject keys must match the keys in questions.py: Physics, Math, Chemistry, Biology,
# Aptitude, English.  English runs on Saturday, after the five weekday subjects.
SCHEDULE = {
    0: "Physics",
    1: "Math",
    2: "Chemistry",
    3: "Biology",
    4: "Aptitude",
    5: "English",
    6: None,
}
SUBJECT_LABELS = {
    "Physics": "Physics",
    "Math": "Mathematics",
    "Chemistry": "Chemistry",
    "Biology": "Biology",
    "Aptitude": "Aptitude / General (Logical) Reasoning",
    "English": "English",
}
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Practice rules
QUIZ_TIME_LIMIT_MINUTES = 20          # personal timer once the student starts
PRACTICE_ATTEMPTS_PER_DAY = 2         # attempts per student per day (for that day's subject)

# Daily cutoff for STARTING a quiz, written in ETHIOPIAN time-telling (12 -> 18:00 on a normal clock)
QUIZ_CUTOFF_ENABLED = True
QUIZ_CUTOFF_ETHIOPIAN_HOUR = 12
QUIZ_CUTOFF_MINUTE = 0

PAYMENT_METHODS = (
    "• Telebirr: 0945065300 - (Ebawak Kibru)\n"
    "• CBE: 1000712174688 - (Natnael Mosisa)\n"
)

# Messages students get when something is switched off
MSG_BOT_OFF = (
    "🔧 <b>ETquizhub is currently under update</b>\n\n"
    "The bot is temporarily not working while we make improvements. Please check back soon. 🙏\n\n"
    "📢 Follow our channel for the latest news on when we're back."
)
MSG_LIVE_CLOSED = (
    "📝 <b>ETquizhub Live registration is closed for now.</b>\n\n"
    "You can still register for the <b>Practice</b> package. "
    "We'll announce on our channel when Live opens."
)
MSG_REG_PAUSED = (
    "🚫 <b>Registration is currently paused.</b>\n\n"
    "Check the channel for when it opens again."
)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ===========================================================================
# SMALL HELPERS
# ===========================================================================
EAT_OFFSET = timedelta(hours=3)
QUIZ_TIME_LIMIT = timedelta(minutes=QUIZ_TIME_LIMIT_MINUTES)
# Ethiopian-clock hour -> normal-clock hour is +6 (12 -> 18)
QUIZ_CUTOFF_TIME = dtime((QUIZ_CUTOFF_ETHIOPIAN_HOUR + 6) % 24, QUIZ_CUTOFF_MINUTE)
LETTERS = "ABCD"


def esc(text):
    """Escape text so it is safe inside a Telegram HTML message."""
    return html.escape(str(text), quote=False)


def now_utc():
    return datetime.now(timezone.utc)


def now_eat():
    """Current date+time in Ethiopia (naive datetime), correct on any server timezone."""
    return (datetime.now(timezone.utc) + EAT_OFFSET).replace(tzinfo=None)


def cutoff_text():
    return f"{QUIZ_CUTOFF_ETHIOPIAN_HOUR}:{QUIZ_CUTOFF_MINUTE:02d} (Ethiopian time)"


def cutoff_has_passed():
    if not QUIZ_CUTOFF_ENABLED:
        return False
    return now_eat().time() >= QUIZ_CUTOFF_TIME


def next_scheduled_day():
    """(weekday_index, subject) of the next session. Today counts if it has a subject and the
    cutoff has not passed. (None, None) if SCHEDULE has nothing."""
    today = now_eat().weekday()
    if SCHEDULE.get(today) and not cutoff_has_passed():
        return today, SCHEDULE[today]
    for offset in range(1, 8):
        day = (today + offset) % 7
        if SCHEDULE.get(day):
            return day, SCHEDULE[day]
    return None, None


def last_quiz_day():
    """(date, subject) of the most recent scheduled session (today counts)."""
    today = now_eat()
    for back in range(0, 8):
        d = today - timedelta(days=back)
        subj = SCHEDULE.get(d.weekday())
        if subj:
            return d.date(), subj
    return None, None


def schedule_text():
    lines = []
    for i in range(7):
        if SCHEDULE.get(i):
            lines.append(f"• {WEEKDAY_NAMES[i]}: {esc(SUBJECT_LABELS.get(SCHEDULE[i], SCHEDULE[i]))}")
    return "\n".join(lines)


def get_questions(tier, subject):
    return QUESTION_SETS.get((tier, subject)) or []


def correct_index(q):
    return q["choices"].index(q["correct"])


def fmt_duration(seconds):
    seconds = int(seconds)
    return f"{seconds // 60}m {seconds % 60:02d}s"


def safe_edit(text, chat_id, message_id, reply_markup=None):
    """Edit a message in place; if that is impossible, send it as a new message."""
    try:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=reply_markup)
        return message_id
    except ApiTelegramException as e:
        if "message is not modified" in str(e):
            return message_id
        sent = bot.send_message(chat_id, text, reply_markup=reply_markup)
        return sent.message_id


def send_long(chat_id, blocks, limit=3800):
    """Send a list of text blocks, joining them into messages below Telegram's size limit."""
    chunk = ""
    for block in blocks:
        if chunk and len(chunk) + len(block) + 2 > limit:
            bot.send_message(chat_id, chunk)
            chunk = ""
        chunk = f"{chunk}\n\n{block}" if chunk else block
    if chunk:
        bot.send_message(chat_id, chunk)


def is_admin(user_id):
    return ADMIN_ID != 0 and user_id == ADMIN_ID


def gated(obj):
    """Master switch. Returns True (and tells the student) when the whole bot is switched off."""
    if BOT_ACTIVE:
        return False
    if isinstance(obj, types.CallbackQuery):
        try:
            bot.answer_callback_query(obj.id, "🔧 ETquizhub is under update.")
        except Exception:
            pass
        chat_id = obj.message.chat.id
    else:
        chat_id = obj.chat.id
    bot.send_message(chat_id, MSG_BOT_OFF)
    return True


# ===========================================================================
# DATABASE (Neon Postgres) - a fresh connection per operation, so Neon going to
# sleep never leaves the bot holding a dead connection.
# ===========================================================================
def get_conn():
    last = None
    for _ in range(4):
        try:
            return psycopg2.connect(DATABASE_URL, connect_timeout=10)
        except psycopg2.OperationalError as e:
            last = e
            time.sleep(1.5)
    raise last


@contextmanager
def tx():
    """One transaction. Yields a cursor returning dict rows; commits on success."""
    conn = get_conn()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run(sql, params=None, fetch=None):
    with tx() as cur:
        cur.execute(sql, params)
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        return cur.rowcount


def init_db():
    with tx() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                name TEXT,
                payer_name TEXT,
                tier CHAR(1),
                step TEXT NOT NULL DEFAULT 'name',
                practice_access BOOLEAN NOT NULL DEFAULT FALSE,
                live_ticket TEXT,
                pending_package TEXT,
                pending_amount INTEGER,
                claim_sent_at TIMESTAMPTZ,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS ticket_counters (
                tier CHAR(1) PRIMARY KEY,
                last_number INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                package TEXT NOT NULL,
                amount INTEGER NOT NULL,
                confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now()
            );
            CREATE TABLE IF NOT EXISTS attempts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES students(user_id),
                tier CHAR(1) NOT NULL,
                subject TEXT NOT NULL,
                quiz_date DATE NOT NULL,
                started_at TIMESTAMPTZ NOT NULL,
                finished_at TIMESTAMPTZ,
                status TEXT NOT NULL DEFAULT 'active',
                current_q INTEGER NOT NULL DEFAULT 0,
                message_id BIGINT,
                answers JSONB NOT NULL DEFAULT '[]'::jsonb,
                score INTEGER
            );
            CREATE INDEX IF NOT EXISTS attempts_user_day_idx ON attempts (user_id, quiz_date, subject);
            """
        )


def get_student(user_id):
    return run("SELECT * FROM students WHERE user_id = %s", (user_id,), "one")


def get_attempt(attempt_id):
    return run("SELECT * FROM attempts WHERE id = %s", (attempt_id,), "one")


# ===========================================================================
# REGISTRATION
# ===========================================================================
def owns_practice(s):
    return bool(s["practice_access"])


def owns_live(s):
    return bool(s["live_ticket"])


def available_packages(s):
    """Packages this student can still buy right now."""
    options = []
    if not owns_practice(s):
        options.append("practice")
    if LIVE_REGISTRATION_OPEN and not owns_live(s):
        options.append("live")
        if not owns_practice(s):
            options.append("both")
    return options


def package_keyboard(s):
    markup = types.InlineKeyboardMarkup()
    for pkg in available_packages(s):
        markup.add(
            types.InlineKeyboardButton(
                f"{PACKAGE_NAMES[pkg]} - {PRICES[pkg]} ETB", callback_data=f"pkg|{pkg}"
            )
        )
    return markup


def package_prompt(s):
    text = (
        f"🎓 <b>{esc(TIERS[s['tier']])}</b>\n\n"
        "Choose your package:\n\n"
        f"📚 <b>Practice - {PRICES['practice']} ETB</b>\n"
        "Daily subject practice quizzes (25 questions each, timed).\n"
    )
    if LIVE_REGISTRATION_OPEN:
        text += (
            f"\n🏟 <b>Live exam - {PRICES['live']} ETB</b>\n"
            "A registration ticket for the in-person exam at your school.\n"
            f"\n🎁 <b>Bundle - {PRICES['both']} ETB</b>\n"
            "Practice + Live with one payment.\n"
        )
    return text


def send_package_choice(chat_id, s):
    if not available_packages(s):
        bot.send_message(chat_id, "✅ You already have everything that's currently available.")
        return
    bot.send_message(chat_id, package_prompt(s), reply_markup=package_keyboard(s))


def show_status(chat_id, s):
    lines = [
        "📋 <b>Your ETquizhub profile</b>",
        "━━━━━━━━━━━━━━",
        f"👤 {esc(s['name'])}",
        f"🎓 {esc(TIERS.get(s['tier'], '-'))}",
        "",
    ]
    pending = s["pending_package"]
    if owns_practice(s):
        lines.append("📚 Practice: ✅ active - send /quiz")
    elif pending in ("practice", "both"):
        lines.append("📚 Practice: ⏳ waiting for payment confirmation")
    else:
        lines.append("📚 Practice: not purchased")
    if owns_live(s):
        lines.append(f"🏟 Live: 🎟 <code>{esc(s['live_ticket'])}</code>")
    elif pending in ("live", "both"):
        lines.append("🏟 Live: ⏳ waiting for payment confirmation")
    elif LIVE_REGISTRATION_OPEN:
        lines.append("🏟 Live: not purchased")
    else:
        lines.append("🏟 Live: registration closed for now")
    if pending:
        lines.append(
            f"\n💰 Pending: <b>{esc(PACKAGE_NAMES[pending])}</b> ({s['pending_amount']} ETB). "
            "After paying, send /paid."
        )
    markup = None
    if available_packages(s) and not REGISTRATION_PAUSED:
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("➕ Get a package", callback_data="buy|start"))
    bot.send_message(chat_id, "\n".join(lines), reply_markup=markup)


@bot.message_handler(commands=["start"])
def cmd_start(message):
    if gated(message):
        return
    user_id = message.from_user.id
    s = get_student(user_id)
    if s and s["step"] == "done":
        show_status(message.chat.id, s)
        return
    if REGISTRATION_PAUSED:
        bot.send_message(message.chat.id, MSG_REG_PAUSED)
        return
    run(
        """
        INSERT INTO students (user_id, username, step) VALUES (%s, %s, 'name')
        ON CONFLICT (user_id) DO UPDATE SET step = 'name', username = EXCLUDED.username
        """,
        (user_id, message.from_user.username),
    )
    bot.send_message(
        message.chat.id,
        "👋 <b>Welcome to ETquizhub!</b>\n\n"
        "Please reply with your <b>full name</b> to register.",
    )


@bot.message_handler(commands=["help"])
def cmd_help(message):
    if gated(message):
        return
    bot.send_message(
        message.chat.id,
        "ℹ️ <b>Commands</b>\n\n"
        "/start - register or see your profile\n"
        "/paid - tell us you've paid\n"
        "/quiz - start today's Practice quiz\n"
        "/timeleft - time left on your current quiz\n"
        "/myticket - show your Live ticket\n\n"
        f"📅 <b>Practice schedule</b>\n{schedule_text()}",
    )


@bot.callback_query_handler(func=lambda c: c.data.startswith("tier|"))
def cb_tier(call):
    if gated(call):
        return
    user_id = call.from_user.id
    s = get_student(user_id)
    if not s or s["step"] != "tier":
        bot.answer_callback_query(call.id)
        return
    tier = call.data.split("|")[1]
    if tier not in TIERS:
        bot.answer_callback_query(call.id)
        return
    run("UPDATE students SET tier = %s, step = 'package' WHERE user_id = %s", (tier, user_id))
    s = get_student(user_id)
    bot.answer_callback_query(call.id)
    safe_edit(package_prompt(s), call.message.chat.id, call.message.message_id,
              reply_markup=package_keyboard(s))


@bot.callback_query_handler(func=lambda c: c.data == "buy|start")
def cb_buy_start(call):
    if gated(call):
        return
    bot.answer_callback_query(call.id)
    if REGISTRATION_PAUSED:
        bot.send_message(call.message.chat.id, MSG_REG_PAUSED)
        return
    s = get_student(call.from_user.id)
    if s and s["step"] == "done":
        send_package_choice(call.message.chat.id, s)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pkg|"))
def cb_package(call):
    if gated(call):
        return
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    s = get_student(user_id)
    if not s or not s["tier"]:
        bot.answer_callback_query(call.id)
        return
    if REGISTRATION_PAUSED:
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, MSG_REG_PAUSED)
        return
    pkg = call.data.split("|")[1]
    if pkg not in PRICES:
        bot.answer_callback_query(call.id)
        return
    if pkg in ("live", "both") and not LIVE_REGISTRATION_OPEN:
        bot.answer_callback_query(call.id)
        bot.send_message(chat_id, MSG_LIVE_CLOSED)
        return
    if pkg not in available_packages(s):
        bot.answer_callback_query(call.id, "That package isn't available for you.")
        return

    amount = PRICES[pkg]
    run(
        """
        UPDATE students SET pending_package = %s, pending_amount = %s, step = 'done',
                            claim_sent_at = NULL
        WHERE user_id = %s
        """,
        (pkg, amount, user_id),
    )
    bot.answer_callback_query(call.id)
    text = (
        f"💰 <b>{esc(PACKAGE_NAMES[pkg])}: {amount} ETB</b>\n\n"
        f"Please pay via one of these:\n{esc(PAYMENT_METHODS)}\n"
        "Then send /paid here to notify us.\n"
        "Once we confirm your payment you'll get a message here."
    )
    if pkg in ("practice", "both"):
        text += (
            f"\n\n📚 Practice: quizzes open on schedule - you must START before "
            f"{cutoff_text()} on the day, then you have {QUIZ_TIME_LIMIT_MINUTES} minutes.\n"
            f"{schedule_text()}"
        )
    safe_edit(text, chat_id, call.message.message_id)


# Registration text steps (name, payer name). Registered LAST among message handlers
# so commands are matched first - see the bottom of the file.
def handle_registration_text(message):
    if gated(message):
        return
    user_id = message.from_user.id
    text = (message.text or "").strip()
    if text.startswith("/"):
        bot.send_message(message.chat.id, "I don't know that command. Send /help to see the list.")
        return
    s = get_student(user_id)
    if not s:
        bot.send_message(message.chat.id, "Please send /start to register.")
        return

    if s["step"] == "name":
        if not (2 <= len(text) <= 60):
            bot.send_message(message.chat.id, "Please send your full name (2-60 characters).")
            return
        run("UPDATE students SET name = %s, step = 'payer' WHERE user_id = %s", (text, user_id))
        bot.send_message(
            message.chat.id,
            f"✅ Registered as <b>{esc(text)}</b>!\n\n"
            "What name is on the account you'll pay from? "
            "(This helps us match your payment.)",
        )
    elif s["step"] == "payer":
        if not (2 <= len(text) <= 60):
            bot.send_message(message.chat.id, "Please send the name on the paying account (2-60 characters).")
            return
        run("UPDATE students SET payer_name = %s, step = 'tier' WHERE user_id = %s", (text, user_id))
        markup = types.InlineKeyboardMarkup()
        for key, label in TIERS.items():
            markup.add(types.InlineKeyboardButton(label, callback_data=f"tier|{key}"))
        bot.send_message(message.chat.id, "🎓 Which tier are you in?", reply_markup=markup)
    elif s["step"] == "tier":
        bot.send_message(message.chat.id, "Please tap one of the tier buttons above (or send /start to begin again).")
    elif s["step"] == "package":
        bot.send_message(message.chat.id, "Please tap one of the package buttons above.")
    else:
        bot.send_message(message.chat.id, "Send /help to see what you can do.")


# ===========================================================================
# PAYMENT
# ===========================================================================
@bot.message_handler(commands=["paid"])
def cmd_paid(message):
    if gated(message):
        return
    user_id = message.from_user.id
    s = get_student(user_id)
    if not s or s["step"] != "done" or not s["pending_package"]:
        bot.send_message(
            message.chat.id,
            "You don't have a package waiting for payment. Send /start to choose one.",
        )
        return
    run("UPDATE students SET claim_sent_at = now() WHERE user_id = %s", (user_id,))
    bot.send_message(
        message.chat.id,
        "📨 Thanks! We've notified the admin to confirm your payment.\n"
        "You'll get a message here once it's confirmed.",
    )
    bot.send_message(
        ADMIN_ID,
        f"💵 <b>Payment claim</b>\n"
        f"Name: {esc(s['name'])}\n"
        f"Payer account name: {esc(s['payer_name'] or '(not provided)')}\n"
        f"Tier: {esc(TIERS.get(s['tier'], '-'))}\n"
        f"Package: {esc(PACKAGE_NAMES[s['pending_package']])} - {s['pending_amount']} ETB\n"
        f"Telegram ID: <code>{user_id}</code>\n\n"
        f"If it's in your payment history, reply with:\n"
        f"/confirm {user_id}",
    )


def ticket_message(name, tier, ticket):
    return (
        "🎟 <b>ETquizhub Live - Your Ticket</b>\n"
        "━━━━━━━━━━━━━━\n"
        f"👤 <b>Name:</b> {esc(name)}\n"
        f"🎓 <b>Tier:</b> {esc(TIERS.get(tier, tier))}\n"
        f"🔖 <b>Ticket No.:</b> <code>{esc(ticket)}</code>\n"
        "━━━━━━━━━━━━━━\n"
        "Keep this ticket safe - it's your proof of registration. "
        "Send /myticket anytime to see it again."
    )


@bot.message_handler(commands=["confirm"])
def cmd_confirm(message):
    if not is_admin(message.from_user.id):
        return
    try:
        target_id = int(message.text.split()[1])
    except (IndexError, ValueError):
        bot.send_message(message.chat.id, "Usage: /confirm <telegram_id>")
        return

    with tx() as cur:
        cur.execute("SELECT * FROM students WHERE user_id = %s FOR UPDATE", (target_id,))
        s = cur.fetchone()
        if not s:
            bot.send_message(message.chat.id, "That user hasn't registered yet.")
            return
        pkg = s["pending_package"]
        if not pkg:
            bot.send_message(message.chat.id, "That student has nothing waiting for confirmation.")
            return
        amount = s["pending_amount"]
        ticket = s["live_ticket"]
        if pkg in ("live", "both") and not ticket:
            cur.execute(
                """
                INSERT INTO ticket_counters (tier, last_number) VALUES (%s, 1)
                ON CONFLICT (tier) DO UPDATE SET last_number = ticket_counters.last_number + 1
                RETURNING last_number
                """,
                (s["tier"],),
            )
            number = cur.fetchone()["last_number"]
            ticket = f"ETQL-{s['tier']}-{number:03d}"
        cur.execute(
            """
            UPDATE students SET
                practice_access = practice_access OR %s,
                live_ticket = %s,
                pending_package = NULL, pending_amount = NULL, claim_sent_at = NULL
            WHERE user_id = %s
            """,
            (pkg in ("practice", "both"), ticket, target_id),
        )
        cur.execute(
            "INSERT INTO payments (user_id, package, amount) VALUES (%s, %s, %s)",
            (target_id, pkg, amount),
        )

    bot.send_message(
        message.chat.id,
        f"✅ Confirmed {esc(PACKAGE_NAMES[pkg])} for {esc(s['name'])} ({target_id})"
        + (f" - ticket <code>{esc(ticket)}</code>" if pkg in ("live", "both") else ""),
    )
    if pkg in ("practice", "both"):
        bot.send_message(
            target_id,
            "✅ <b>Payment confirmed!</b> Practice is unlocked.\n\n"
            "Send /quiz on a session day to start (you must start before "
            f"{cutoff_text()}).\n\n📅 <b>Schedule</b>\n{schedule_text()}",
        )
    else:
        bot.send_message(target_id, "✅ <b>Payment confirmed!</b>")
    if pkg in ("live", "both"):
        bot.send_message(target_id, ticket_message(s["name"], s["tier"], ticket))


@bot.message_handler(commands=["myticket"])
def cmd_myticket(message):
    if gated(message):
        return
    s = get_student(message.from_user.id)
    if s and s["live_ticket"]:
        bot.send_message(message.chat.id, ticket_message(s["name"], s["tier"], s["live_ticket"]))
    else:
        bot.send_message(message.chat.id, "You don't have a Live ticket yet.")


# ===========================================================================
# PRACTICE QUIZ
# ===========================================================================
def attempts_used_today(user_id, subject):
    row = run(
        "SELECT COUNT(*) AS n FROM attempts WHERE user_id = %s AND quiz_date = %s AND subject = %s",
        (user_id, now_eat().date(), subject),
        "one",
    )
    return row["n"]


def attempt_timed_out(a):
    return now_utc() - a["started_at"] > QUIZ_TIME_LIMIT


@bot.message_handler(commands=["quiz"])
def cmd_quiz(message):
    if gated(message):
        return
    user_id = message.from_user.id
    chat_id = message.chat.id
    s = get_student(user_id)
    if not s or s["step"] != "done":
        bot.send_message(chat_id, "Please /start first to register.")
        return
    if not owns_practice(s):
        if s["pending_package"] in ("practice", "both"):
            bot.send_message(
                chat_id,
                "⏳ Your payment hasn't been confirmed yet. Please wait, or send /paid if you haven't already.",
            )
        else:
            bot.send_message(chat_id, "📚 Practice isn't on your account yet. Send /start to get the Practice package.")
        return

    # 1) A quiz already in progress is resumed (or closed if its time ran out).
    #    This comes before the cutoff check, so students mid-quiz are never cut off.
    active = run(
        "SELECT * FROM attempts WHERE user_id = %s AND status = 'active' ORDER BY id DESC LIMIT 1",
        (user_id,),
        "one",
    )
    if active:
        if attempt_timed_out(active):
            finalize_attempt(active["id"], chat_id, timed_out=True)
            return
        run("UPDATE attempts SET message_id = NULL WHERE id = %s", (active["id"],))
        send_question(chat_id, get_attempt(active["id"]))
        return

    # 2) Day gate
    today_subject = SCHEDULE.get(now_eat().weekday())
    if today_subject is None:
        weekday, subject = next_scheduled_day()
        if subject:
            bot.send_message(
                chat_id,
                f"📅 No quiz today. Next up: <b>{esc(SUBJECT_LABELS.get(subject, subject))}</b> on "
                f"{WEEKDAY_NAMES[weekday]}.",
            )
        else:
            bot.send_message(chat_id, "📅 No quiz is scheduled right now. Check the channel for updates.")
        return

    # 3) Cutoff only blocks NEW starts
    if cutoff_has_passed():
        bot.send_message(
            chat_id,
            f"⛔ Today's entry window closed at {cutoff_text()}. You can't start a new quiz today.",
        )
        return

    # 4) Attempts and questions
    questions = get_questions(s["tier"], today_subject)
    if not questions:
        bot.send_message(chat_id, "🛠 Today's questions aren't ready yet. Please check back soon.")
        return
    used = attempts_used_today(user_id, today_subject)
    if used >= PRACTICE_ATTEMPTS_PER_DAY:
        bot.send_message(
            chat_id,
            f"🔒 You've used all {PRACTICE_ATTEMPTS_PER_DAY} attempts for today's "
            f"{esc(SUBJECT_LABELS.get(today_subject, today_subject))} quiz. See you at the next session!",
        )
        return

    row = run(
        """
        INSERT INTO attempts (user_id, tier, subject, quiz_date, started_at)
        VALUES (%s, %s, %s, %s, %s) RETURNING id
        """,
        (user_id, s["tier"], today_subject, now_eat().date(), now_utc()),
        "one",
    )
    left = PRACTICE_ATTEMPTS_PER_DAY - used - 1
    bot.send_message(
        chat_id,
        f"🚀 <b>{esc(SUBJECT_LABELS.get(today_subject, today_subject))}</b> - {len(questions)} questions, "
        f"{QUIZ_TIME_LIMIT_MINUTES} minutes. Your timer has started!\n"
        f"Attempts left today after this one: {left}",
    )
    send_question(chat_id, get_attempt(row["id"]))


def question_text(a, qi, questions):
    q = questions[qi]
    lines = [
        f"📘 <b>Question {qi + 1}/{len(questions)}</b> · {esc(SUBJECT_LABELS.get(a['subject'], a['subject']))}",
        "━━━━━━━━━━━━━━",
        "",
        esc(q["text"]),
        "",
    ]
    for i, choice in enumerate(q["choices"]):
        lines.append(f"<b>{LETTERS[i]})</b> {esc(choice)}")
    return "\n".join(lines)


def send_question(chat_id, a):
    questions = get_questions(a["tier"], a["subject"])
    qi = a["current_q"]
    if qi >= len(questions):
        finalize_attempt(a["id"], chat_id)
        return
    markup = types.InlineKeyboardMarkup()
    markup.row(
        *[
            types.InlineKeyboardButton(LETTERS[i], callback_data=f"ans|{a['id']}|{qi}|{i}")
            for i in range(len(questions[qi]["choices"]))
        ]
    )
    text = question_text(a, qi, questions)
    if a["message_id"]:
        new_id = safe_edit(text, chat_id, a["message_id"], reply_markup=markup)
    else:
        new_id = bot.send_message(chat_id, text, reply_markup=markup).message_id
    if new_id != a["message_id"]:
        run("UPDATE attempts SET message_id = %s WHERE id = %s", (new_id, a["id"]))


@bot.callback_query_handler(func=lambda c: c.data.startswith("ans|"))
def cb_answer(call):
    if gated(call):
        return
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    _, attempt_id, qi, idx = call.data.split("|")
    attempt_id, qi, idx = int(attempt_id), int(qi), int(idx)

    a = get_attempt(attempt_id)
    if not a or a["user_id"] != user_id or a["status"] != "active":
        bot.answer_callback_query(call.id, "This quiz has already ended.")
        return
    if attempt_timed_out(a):
        bot.answer_callback_query(call.id, "⏰ Time's up!")
        finalize_attempt(attempt_id, chat_id, timed_out=True)
        return
    questions = get_questions(a["tier"], a["subject"])
    if qi != a["current_q"] or qi >= len(questions):
        bot.answer_callback_query(call.id, "That question was already answered.")
        return

    # Atomic: only the first tap on this question is recorded (guards against double taps)
    row = run(
        """
        UPDATE attempts SET answers = answers || jsonb_build_array(%s::int), current_q = current_q + 1
        WHERE id = %s AND current_q = %s AND status = 'active'
        RETURNING current_q
        """,
        (idx, attempt_id, qi),
        "one",
    )
    if not row:
        bot.answer_callback_query(call.id, "Already recorded.")
        return

    q = questions[qi]
    right = correct_index(q)
    if idx == right:
        bot.answer_callback_query(call.id, "✅ Correct! 🔥")
    else:
        correct_label = f"{LETTERS[right]}) {q['choices'][right]}"
        bot.answer_callback_query(call.id, f"❌ Incorrect - answer was {correct_label}"[:190])

    if row["current_q"] < len(questions):
        send_question(chat_id, get_attempt(attempt_id))
    else:
        finalize_attempt(attempt_id, chat_id)


def finalize_attempt(attempt_id, chat_id, timed_out=False):
    a = get_attempt(attempt_id)
    if not a or a["status"] != "active":
        return
    questions = get_questions(a["tier"], a["subject"])
    answers = a["answers"] or []
    score = 0
    for i, idx in enumerate(answers[: len(questions)]):
        if idx == correct_index(questions[i]):
            score += 1
    status = "timed_out" if timed_out else "finished"
    done = run(
        """
        UPDATE attempts SET status = %s, finished_at = %s, score = %s
        WHERE id = %s AND status = 'active' RETURNING id
        """,
        (status, now_utc(), score, attempt_id),
        "one",
    )
    if not done:
        return  # already finalized by another tap

    total = len(questions)
    reached = len(answers)
    pct = (score / reached * 100) if reached else 0
    if timed_out:
        verdict = "⏰ <b>Time ran out - here's how far you got:</b>"
    elif pct >= 90:
        verdict = "🏆 <b>Top performer!</b>"
    elif pct >= 70:
        verdict = "👏 <b>Strong finish!</b>"
    else:
        verdict = "📚 <b>Solid attempt - keep practicing!</b>"

    summary = (
        f"🎯 <b>Practice complete!</b>\n━━━━━━━━━━━━━━\n\n"
        f"{esc(SUBJECT_LABELS.get(a['subject'], a['subject']))} · {esc(TIERS.get(a['tier'], a['tier']))}\n"
        f"Your score: <b>{score}/{total}</b> ({reached} of {total} questions reached)\n{verdict}"
    )
    if a["message_id"]:
        safe_edit(summary, chat_id, a["message_id"])
    else:
        bot.send_message(chat_id, summary)

    blocks = []
    for i, idx in enumerate(answers[: len(questions)]):
        q = questions[i]
        right = correct_index(q)
        icon = "✅" if idx == right else "❌"
        chosen = f"{LETTERS[idx]}) {q['choices'][idx]}" if 0 <= idx < len(q["choices"]) else "-"
        blocks.append(
            f"{icon} <b>Q{i + 1}:</b> {esc(q['text'])}\n"
            f"   Your answer: {esc(chosen)}\n"
            f"   Correct: {esc(LETTERS[right])}) {esc(q['choices'][right])}"
        )
    if blocks:
        send_long(chat_id, blocks)

    s = get_student(a["user_id"])
    who = esc(s["name"]) if s else str(a["user_id"])
    note = " (timed out)" if timed_out else ""
    try:
        bot.send_message(
            ADMIN_ID,
            f"📥 <b>{who}</b> ({esc(a['tier'])}) finished {esc(a['subject'])}{note}: "
            f"<b>{score}/{total}</b>",
        )
    except Exception:
        pass


@bot.message_handler(commands=["timeleft"])
def cmd_timeleft(message):
    if gated(message):
        return
    a = run(
        "SELECT * FROM attempts WHERE user_id = %s AND status = 'active' ORDER BY id DESC LIMIT 1",
        (message.from_user.id,),
        "one",
    )
    if not a:
        bot.send_message(message.chat.id, "You don't have an active quiz right now.")
        return
    remaining = QUIZ_TIME_LIMIT - (now_utc() - a["started_at"])
    if remaining.total_seconds() <= 0:
        bot.send_message(message.chat.id, "⏰ Your time is already up! Tap any answer to submit.")
        return
    bot.send_message(message.chat.id, f"⏱ Time remaining: <b>{fmt_duration(remaining.total_seconds())}</b>")


# ===========================================================================
# ADMIN: leaderboard + status
# ===========================================================================
@bot.message_handler(commands=["leaderboard"])
def cmd_leaderboard(message):
    if not is_admin(message.from_user.id):
        return
    day, subject = last_quiz_day()
    if not subject:
        bot.send_message(message.chat.id, "No scheduled session found.")
        return
    parts = message.text.split()
    tiers = [parts[1].upper()] if len(parts) > 1 and parts[1].upper() in TIERS else list(TIERS)
    medals = ["🥇", "🥈", "🥉"]
    blocks = [f"🏆 <b>{esc(SUBJECT_LABELS.get(subject, subject))}</b> - {day}"]
    for tier in tiers:
        rows = run(
            """
            SELECT DISTINCT ON (a.user_id) s.name, a.score,
                   EXTRACT(EPOCH FROM (a.finished_at - a.started_at)) AS secs
            FROM attempts a JOIN students s ON s.user_id = a.user_id
            WHERE a.quiz_date = %s AND a.tier = %s AND a.subject = %s
              AND a.status IN ('finished', 'timed_out')
            ORDER BY a.user_id, a.score DESC, secs ASC
            """,
            (day, tier, subject),
            "all",
        )
        rows = sorted(rows, key=lambda r: (-r["score"], r["secs"]))
        total = len(get_questions(tier, subject))
        lines = [f"<b>{esc(TIERS[tier])}</b>"]
        if not rows:
            lines.append("No one has finished yet.")
        for i, r in enumerate(rows):
            icon = medals[i] if i < 3 else f"{i + 1}."
            lines.append(f"{icon} {esc(r['name'])} - <b>{r['score']}/{total}</b> ({fmt_duration(r['secs'])})")
        lines.append(f"👥 Finished: {len(rows)}")
        blocks.append("\n".join(lines))
    send_long(message.chat.id, blocks)


@bot.message_handler(commands=["status"])
def cmd_status(message):
    if not is_admin(message.from_user.id):
        return
    c = run(
        """
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE tier = 'A') AS tier_a,
               COUNT(*) FILTER (WHERE tier = 'B') AS tier_b,
               COUNT(*) FILTER (WHERE practice_access) AS practice,
               COUNT(*) FILTER (WHERE live_ticket IS NOT NULL) AS live,
               COUNT(*) FILTER (WHERE pending_package IS NOT NULL) AS pending
        FROM students WHERE step = 'done'
        """,
        None,
        "one",
    )
    on, off = "🟢 ON", "🔴 OFF"
    bot.send_message(
        message.chat.id,
        "⚙️ <b>System status</b>\n"
        f"Whole bot: {on if BOT_ACTIVE else off}\n"
        f"Live registration: {on if LIVE_REGISTRATION_OPEN else off}\n"
        f"Registration paused: {'🔴 YES' if REGISTRATION_PAUSED else '🟢 no'}\n\n"
        f"👥 Registered: {c['total']} (A: {c['tier_a']}, B: {c['tier_b']})\n"
        f"📚 Practice active: {c['practice']}\n"
        f"🎟 Live tickets: {c['live']}\n"
        f"⏳ Waiting for confirmation: {c['pending']}",
    )


# Catch-all for plain text (registration steps). MUST stay after every command handler.
@bot.message_handler(content_types=["text"])
def on_text(message):
    handle_registration_text(message)


# ===========================================================================
# RENDER KEEP-ALIVE (tiny web server so Render sees an open port)
# ===========================================================================
web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "ETquizhub bot is running."


def run_web():
    port = int(os.environ.get("PORT", 8080))
    web_app.run(host="0.0.0.0", port=port)


# ===========================================================================
# RUN
# ===========================================================================
if __name__ == "__main__":
    if not BOT_TOKEN or not DATABASE_URL:
        raise SystemExit("BOT_TOKEN and DATABASE_URL environment variables must be set.")
    init_db()
    threading.Thread(target=run_web, daemon=True).start()
    print("Bot is running...")
    bot.infinity_polling()
