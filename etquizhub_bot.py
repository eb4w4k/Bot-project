"""
ETquizhub_bot - Physics/Science Tournament Telegram Bot
=========================================================

WHAT THIS BOT DOES (in order):
1. Student sends /start -> bot asks for their name (registration)
2. Bot asks for the name on the account they'll pay from (for matching)
3. Student is told to pay the entry fee (Telebirr or another bank) and
   send /paid to notify the admin
4. YOU (the admin) manually confirm their payment by running a command
5. Once confirmed, student can type /quiz to begin - BUT ONLY if it's
   still before the daily cutoff time (12:00 PM Ethiopian time). No new
   quiz can be STARTED after that, no matter how early they paid.
6. The moment they start, a personal timer begins for THAT student only
   (QUIZ_TIME_LIMIT_MINUTES, edit it below - e.g. 20). If they started
   before the 12:00 cutoff, they keep their FULL time limit even if it
   runs past 12:00 - the cutoff only blocks NEW starts, never cuts off
   someone already mid-quiz.
7. Questions appear ONE AT A TIME as tap-able buttons - when they tap an
   answer, that SAME message updates to show the next question (no new
   messages pile up in the chat)
8. If their personal timer runs out before they finish, their NEXT tap
   is rejected and whatever they answered so far is auto-submitted as
   their final result.
9. At the end (finished normally or timed out), one final message shows
   their score + every question with what they chose vs the correct answer
10. As each student finishes, YOU (the admin) get an instant notification
    with their name and score
11. YOU can send /leaderboard anytime to see every finished student ranked
    highest to lowest, with medal emojis for top 3
12. After the 12:00 cutoff, YOU manually check the leaderboard, call the
    1st and 2nd place students live on Telegram to verify them with new
    on-the-spot questions, then pay out prizes.

===========================================================================
SETUP STEPS (do these in order):
===========================================================================
1. Install the library:
   Open a terminal in VS Code (Terminal > New Terminal) and run:
       py -3.12 -m pip install -r requirements.txt

2. Get your bot token from @BotFather on Telegram (you already have this
   since your bot ETquizhub_bot already exists).

3. Replace "YOUR_BOT_TOKEN_HERE" below with your real token.

4. Replace "YOUR_TELEGRAM_USER_ID" below with YOUR Telegram numeric ID
   (so the bot knows YOU are the admin allowed to confirm payments).
   -> To find your ID: message @userinfobot on Telegram, it replies with
      your numeric ID instantly.

5. Fill in PAYMENT_METHODS below with your real Telebirr number, CBE
   account, and any other bank details.

6. Set QUIZ_TIME_LIMIT_MINUTES and QUIZ_CUTOFF_HOUR/MINUTE below for
   this tournament.

7. Edit the QUESTIONS list near the bottom with your real questions for
   this tournament.

8. Run the file in VS Code (Run > Run Without Debugging, or press F5, or
   in the terminal: py -3.12 etquizhub_bot.py).
   Your bot is now live as long as this is running. Later, this exact
   file is what you'll deploy to Render so it runs without your PC.

===========================================================================
HOW PAYMENT CONFIRMATION WORKS (manual, since transfers aren't automated):
===========================================================================
- Student pays you outside the bot (normal transfer via any listed method)
- Student sends /paid in the bot -> bot tells you (the admin) their name,
  the name on the paying account, and their Telegram ID
- YOU check your payment history (Telebirr/bank) to confirm they actually paid
- YOU then run: /confirm <their_telegram_id>   (as a message to the bot)
- Bot marks them as confirmed and unlocks /quiz for them (as long as it's
  still before the daily cutoff time)

===========================================================================
HOW THE TIME LIMIT + CUTOFF WORK (no scheduler, just plain checks):
===========================================================================
- QUIZ_TIME_LIMIT_MINUTES: how long a student has to finish once they
  personally start. Checked every time they tap an answer - if too much
  time has passed since THEIR start, their quiz ends right there with
  whatever they've answered so far.
- QUIZ_CUTOFF_HOUR / QUIZ_CUTOFF_MINUTE: the last moment (Ethiopian time)
  someone is allowed to type /quiz and begin. Anyone who already started
  before this keeps their full time limit even if it runs past cutoff.
  Anyone who has NOT started yet is blocked from starting once this time
  passes.
- Ethiopia does not use daylight saving time, so EAT is always UTC+3.
  This is calculated from UTC directly so it works correctly no matter
  what timezone the server (e.g. Render) itself is running in.
"""

import telebot
from telebot import types
from datetime import datetime, timedelta, time as dtime
import os
import threading
from flask import Flask

# ===========================================================================
# CONFIGURATION - edit these things
# ===========================================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))  # falls back to 0 if not set (bot won't recognize any admin)
ENTRY_FEE = 100  # in ETB

# ---------------------------------------------------------------------------
# ONE-LINE PAUSE SWITCH
# Set this to True whenever there's NO tournament running this week (holiday,
# break, or you just haven't set up the next one yet). Set back to False
# when you're ready to run tournaments again. That's it - this one flag
# blocks ALL registration no matter what day/time it is.
# ---------------------------------------------------------------------------
TOURNAMENT_PAUSED = False

# ---------------------------------------------------------------------------
# WEEKLY SCHEDULE - which subject runs on which day.
# Monday=0 ... Sunday=6. Any day not listed here (or set to None) means
# no tournament that day.
# ---------------------------------------------------------------------------
SCHEDULE = {
    0: "Physics",                          # Monday
    1: "Math",                             # Tuesday
    2: "Chemistry",                        # Wednesday
    3: "Biology",                          # Thursday
    4: "Aptitude / General (Logical) Reasoning",  # Friday
    5: None,                               # Saturday - no tournament
    6: None,                               # Sunday - no tournament
}

# How long (in minutes) each student gets to finish once THEY start.
# Change this per tournament/subject if needed.
QUIZ_TIME_LIMIT_MINUTES = 20

# Last moment (Ethiopian local time, 24h) a student may START the quiz.
# No new starts allowed at or after this time. Students already mid-quiz
# are NOT affected - they keep their full QUIZ_TIME_LIMIT_MINUTES.
QUIZ_CUTOFF_HOUR = 12
QUIZ_CUTOFF_MINUTE = 0

# List every payment method students can use. Edit these lines with your
# real account details.
PAYMENT_METHODS = (
    "• Telebirr: [YOUR TELEBIRR NUMBER]\n"
    "• CBE: [YOUR CBE ACCOUNT NUMBER] ([YOUR NAME])\n"
    "• [ANOTHER BANK NAME]: [ACCOUNT NUMBER] ([YOUR NAME])"
)

bot = telebot.TeleBot(BOT_TOKEN)

# ===========================================================================
# TIME HELPERS - Ethiopia is fixed UTC+3, no daylight saving
# ===========================================================================
EAT_OFFSET = timedelta(hours=3)
QUIZ_TIME_LIMIT = timedelta(minutes=QUIZ_TIME_LIMIT_MINUTES)
QUIZ_CUTOFF_TIME = dtime(QUIZ_CUTOFF_HOUR, QUIZ_CUTOFF_MINUTE)


def now_eat():
    """Current date+time in Ethiopia (EAT), computed from UTC so it's
    correct no matter what timezone the machine running this bot is set to."""
    return datetime.utcnow() + EAT_OFFSET


def cutoff_has_passed():
    """True once we're at or past today's cutoff clock time in Ethiopia."""
    return now_eat().time() >= QUIZ_CUTOFF_TIME


def get_active_subject():
    """Returns today's subject name if a tournament is currently open for
    NEW registration, or None if closed. Checked every time someone tries
    to /start. Combines three things, in order:
      1. TOURNAMENT_PAUSED - the master switch, overrides everything
      2. SCHEDULE - is a subject even assigned to today?
      3. cutoff time - are we still before today's cutoff?
    """
    if TOURNAMENT_PAUSED:
        return None

    subject = SCHEDULE.get(now_eat().weekday())
    if subject is None:
        return None

    if cutoff_has_passed():
        return None

    return subject


def student_timed_out(user_id):
    """True if this student personally started and their time is up."""
    start = students.get(user_id, {}).get("quiz_start_time")
    if not start:
        return False
    return now_eat() - start > QUIZ_TIME_LIMIT


# ===========================================================================
# IN-MEMORY STORAGE
# For your first tournament (50 students) this is totally fine.
# Everything resets if the bot restarts - later you can upgrade this to
# a real database, but don't worry about that yet.
# ===========================================================================
students = {}
# students[user_id] = {
#     "name": str,
#     "payer_name": str,       # name on the account they pay from
#     "paid_confirmed": bool,
#     "quiz_active": bool,
#     "quiz_start_time": datetime or None,   # THEIR personal start time (EAT)
#     "current_question": int,
#     "message_id": int,       # the ONE message that gets edited each question
#     "results": list of dicts
# }

# ===========================================================================
# YOUR QUESTIONS - edit this for each tournament
# "choices" = the tap-able buttons, "correct" must match one choice exactly
# ===========================================================================
QUESTIONS = [
    {
        "text": "A car accelerates from 0 to 20 m/s in 4 seconds. What is its acceleration?",
        "choices": ["2 m/s²", "5 m/s²", "8 m/s²", "80 m/s²"],
        "correct": "5 m/s²",
    },
    {
        "text": "What is the SI unit of force?",
        "choices": ["Joule", "Watt", "Newton", "Pascal"],
        "correct": "Newton",
    },
    {
        "text": "An object in free fall (ignoring air resistance) has acceleration approximately:",
        "choices": ["9.8 m/s²", "10 km/s²", "1 m/s²", "0 m/s²"],
        "correct": "9.8 m/s²",
    },
    {
        "text": "Which of these is a vector quantity?",
        "choices": ["Mass", "Speed", "Velocity", "Energy"],
        "correct": "Velocity",
    },
    {
        "text": "Kinetic energy formula is:",
        "choices": ["mgh", "½mv²", "F/m", "Fd"],
        "correct": "½mv²",
    },
]


# ===========================================================================
# STEP 1: REGISTRATION - name
# ===========================================================================
@bot.message_handler(commands=["start"])
def start(message):
    user_id = message.from_user.id

    subject = get_active_subject()
    if subject is None:
        bot.send_message(
            message.chat.id,
            "🚫 *No tournament running right now.*\n\n"
            "Weekly schedule:\n"
            "Mon - Physics | Tue - Math | Wed - Chemistry\n"
            "Thu - Biology | Fri - Aptitude/General Reasoning\n\n"
            f"Registration opens each day until "
            f"{QUIZ_CUTOFF_HOUR:02d}:{QUIZ_CUTOFF_MINUTE:02d} Ethiopian time. "
            "Check the channel for updates.",
            parse_mode="Markdown",
        )
        return

    students[user_id] = {
        "name": None,
        "payer_name": None,
        "paid_confirmed": False,
        "quiz_active": False,
        "quiz_start_time": None,
        "current_question": 0,
        "message_id": None,
        "results": [],
    }
    bot.send_message(
        message.chat.id,
        "👋 *Welcome to ETquizhub!*\n\n"
        "Please reply with your *full name* to register.",
        parse_mode="Markdown",
    )
    bot.register_next_step_handler(message, save_name)


def save_name(message):
    user_id = message.from_user.id
    name = message.text.strip()
    students[user_id]["name"] = name

    bot.send_message(
        message.chat.id,
        f"✅ Registered as *{name}*!\n\n"
        "What name is on the account you'll pay from? "
        "(This helps us match your payment.)",
        parse_mode="Markdown",
    )
    bot.register_next_step_handler(message, save_payer_name)


def save_payer_name(message):
    user_id = message.from_user.id
    payer_name = message.text.strip()
    students[user_id]["payer_name"] = payer_name

    bot.send_message(
        message.chat.id,
        f"💰 *Entry fee: {ENTRY_FEE} ETB*\n\n"
        f"Please pay via one of these:\n{PAYMENT_METHODS}\n\n"
        "Then send /paid here to notify us.\n"
        "Once confirmed, you can start with /quiz\n\n"
        f"⏰ You must START the quiz before "
        f"{QUIZ_CUTOFF_HOUR:02d}:{QUIZ_CUTOFF_MINUTE:02d} (Ethiopian time) today. "
        f"Once you start, you'll have {QUIZ_TIME_LIMIT_MINUTES} minutes to finish.",
        parse_mode="Markdown",
    )


# ===========================================================================
# STEP 2: PAYMENT NOTIFICATION (student side)
# ===========================================================================
@bot.message_handler(commands=["paid"])
def notify_payment(message):
    user_id = message.from_user.id
    if user_id not in students or not students[user_id]["name"]:
        bot.send_message(message.chat.id, "Please /start first to register your name.")
        return

    name = students[user_id]["name"]
    payer_name = students[user_id].get("payer_name") or "(not provided)"

    bot.send_message(
        message.chat.id,
        "📨 Thanks! We've notified the admin to confirm your payment.\n"
        "You'll be able to use /quiz once confirmed.",
    )
    # Notify YOU (the admin) so you know who to check in your payment history
    bot.send_message(
        ADMIN_ID,
        f"💵 Payment claim:\n"
        f"Name: {name}\n"
        f"Payer account name: {payer_name}\n"
        f"Telegram ID: {user_id}\n\n"
        f"If confirmed in your payment history, reply here with:\n"
        f"/confirm {user_id}",
    )


# ===========================================================================
# STEP 3: PAYMENT CONFIRMATION (admin side only - that's you)
# ===========================================================================
@bot.message_handler(commands=["confirm"])
def confirm_payment(message):
    if message.from_user.id != ADMIN_ID:
        return  # silently ignore if anyone else tries this

    try:
        target_id = int(message.text.split()[1])
    except (IndexError, ValueError):
        bot.send_message(message.chat.id, "Usage: /confirm <telegram_id>")
        return

    if target_id in students:
        students[target_id]["paid_confirmed"] = True
        bot.send_message(message.chat.id, f"✅ Confirmed payment for {target_id}")
        bot.send_message(
            target_id,
            "✅ *Payment confirmed!* You're entered.\n\n"
            f"Send /quiz whenever you're ready to start your timed quiz "
            f"(you must start before "
            f"{QUIZ_CUTOFF_HOUR:02d}:{QUIZ_CUTOFF_MINUTE:02d} Ethiopian time today).",
            parse_mode="Markdown",
        )
    else:
        bot.send_message(message.chat.id, "That user hasn't registered yet.")


# ===========================================================================
# STEP 4: START THE QUIZ
# ===========================================================================
@bot.message_handler(commands=["quiz"])
def start_quiz(message):
    user_id = message.from_user.id

    if user_id not in students or not students[user_id]["name"]:
        bot.send_message(message.chat.id, "Please /start first to register.")
        return

    if not students[user_id]["paid_confirmed"]:
        bot.send_message(
            message.chat.id,
            "⏳ Your payment hasn't been confirmed yet. Please wait, or send /paid if you haven't already.",
        )
        return

    # Cutoff only blocks NEW starts. A student who is already mid-quiz
    # (quiz_start_time already set) is never touched by this check.
    if cutoff_has_passed():
        bot.send_message(
            message.chat.id,
            f"⛔ Today's entry window has closed "
            f"({QUIZ_CUTOFF_HOUR:02d}:{QUIZ_CUTOFF_MINUTE:02d} Ethiopian time cutoff). "
            "You can't start a new quiz today. Winners will be announced after "
            "the admin reviews the leaderboard and does live verification calls.",
        )
        return

    students[user_id]["quiz_active"] = True
    students[user_id]["quiz_start_time"] = now_eat()
    students[user_id]["current_question"] = 0
    students[user_id]["results"] = []

    send_question(message.chat.id, user_id, 0)


# ===========================================================================
# STEP 5: SEND / EDIT A QUESTION (this is the "replaces in place" part)
# ===========================================================================
def send_question(chat_id, user_id, question_num):
    q = QUESTIONS[question_num]

    # Build the tap-able answer buttons
    markup = types.InlineKeyboardMarkup()
    for choice in q["choices"]:
        markup.add(
            types.InlineKeyboardButton(
                choice, callback_data=f"ans|{question_num}|{choice}"
            )
        )

    text = (
        f"📘 *Question {question_num + 1}/{len(QUESTIONS)}*\n"
        f"━━━━━━━━━━━━━━\n\n"
        f"{q['text']}"
    )

    existing_message_id = students[user_id]["message_id"]

    if existing_message_id:
        # EDIT the same message -> this is what makes it "replace in place"
        bot.edit_message_text(
            text,
            chat_id,
            existing_message_id,
            parse_mode="Markdown",
            reply_markup=markup,
        )
    else:
        # First question - send a new message, remember its ID
        sent = bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)
        students[user_id]["message_id"] = sent.message_id


# ===========================================================================
# STEP 6: HANDLE A TAPPED ANSWER
# ===========================================================================
@bot.callback_query_handler(func=lambda call: call.data.startswith("ans|"))
def handle_answer(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id

    # Ignore stray taps if their quiz isn't active (already finished/reset)
    if not students.get(user_id, {}).get("quiz_active"):
        bot.answer_callback_query(call.id, "This quiz has already ended.")
        return

    # Personal timer check - independent of the daily cutoff.
    if student_timed_out(user_id):
        bot.answer_callback_query(call.id, "⏰ Time's up!")
        show_final_results(chat_id, user_id, timed_out=True)
        return

    _, question_num_str, chosen = call.data.split("|", 2)
    question_num = int(question_num_str)
    q = QUESTIONS[question_num]

    is_correct = chosen == q["correct"]

    # Save this answer to their results
    students[user_id]["results"].append(
        {
            "question": q["text"],
            "chosen": chosen,
            "correct_answer": q["correct"],
            "is_correct": is_correct,
        }
    )

    # Small popup feedback (doesn't add a chat message)
    if is_correct:
        bot.answer_callback_query(call.id, "✅ Correct! 🔥")
    else:
        bot.answer_callback_query(call.id, f"❌ Incorrect — answer was {q['correct']}")

    # Move to next question, or show final results
    next_q = question_num + 1
    if next_q < len(QUESTIONS):
        send_question(chat_id, user_id, next_q)
    else:
        show_final_results(chat_id, user_id)


# ===========================================================================
# STEP 7: FINAL RESULTS
# ===========================================================================
def show_final_results(chat_id, user_id, timed_out=False):
    results = students[user_id]["results"]
    score = sum(1 for r in results if r["is_correct"])
    total = len(results)
    # Percentage is against how many they actually answered - relevant when
    # they timed out early with only some questions answered.
    percentage = (score / total) * 100 if total else 0

    if timed_out:
        tier = "⏰ *Time ran out - here's how far you got:*"
    elif percentage >= 90:
        tier = "🏆 *Top performer!*"
    elif percentage >= 70:
        tier = "👏 *Strong finish!*"
    else:
        tier = "📚 *Solid attempt — next tournament's yours!*"

    lines = [
        f"🎯 *Quiz Complete!*\n━━━━━━━━━━━━━━\n\n"
        f"Your score: *{score}/{len(QUESTIONS)}* "
        f"({total} of {len(QUESTIONS)} questions reached)\n{tier}\n"
    ]

    for i, r in enumerate(results, 1):
        icon = "✅" if r["is_correct"] else "❌"
        lines.append(
            f"{icon} *Q{i}:* {r['question']}\n"
            f"   Your answer: {r['chosen']} | Correct: {r['correct_answer']}"
        )

    final_text = "\n\n".join(lines)

    message_id = students[user_id]["message_id"]
    bot.edit_message_text(final_text, chat_id, message_id, parse_mode="Markdown")

    students[user_id]["quiz_active"] = False
    students[user_id]["message_id"] = None  # reset for next tournament
    students[user_id]["final_score"] = score  # remember for the leaderboard

    # Notify YOU (the admin) immediately as each student finishes
    name = students[user_id]["name"]
    status = " (timed out)" if timed_out else ""
    bot.send_message(
        ADMIN_ID,
        f"📥 *{name}* finished{status}!\nScore: *{score}/{len(QUESTIONS)}*",
        parse_mode="Markdown",
    )


# ===========================================================================
# STEP 7b: /timeleft - student checks how much time they have remaining
# ===========================================================================
@bot.message_handler(commands=["timeleft"])
def time_left(message):
    user_id = message.from_user.id
    data = students.get(user_id)

    if not data or not data.get("quiz_active") or not data.get("quiz_start_time"):
        bot.send_message(message.chat.id, "You don't have an active quiz right now.")
        return

    remaining = QUIZ_TIME_LIMIT - (now_eat() - data["quiz_start_time"])

    if remaining.total_seconds() <= 0:
        bot.send_message(message.chat.id, "⏰ Your time is already up! Tap any answer to submit.")
        return

    minutes = int(remaining.total_seconds() // 60)
    seconds = int(remaining.total_seconds() % 60)
    bot.send_message(message.chat.id, f"⏱ Time remaining: *{minutes}m {seconds}s*", parse_mode="Markdown")


# ===========================================================================
# STEP 8: LEADERBOARD (admin only) - see everyone's results, ranked
# ===========================================================================
@bot.message_handler(commands=["leaderboard"])
def leaderboard(message):
    if message.from_user.id != ADMIN_ID:
        return  # silently ignore if anyone else tries this

    # Only include students who have actually finished a quiz
    finished = [
        (data["name"], data["final_score"])
        for data in students.values()
        if "final_score" in data
    ]

    if not finished:
        bot.send_message(message.chat.id, "No one has finished the quiz yet.")
        return

    # Sort highest score first
    finished.sort(key=lambda x: x[1], reverse=True)

    lines = ["🏆 *Leaderboard*\n━━━━━━━━━━━━━━"]
    medals = ["🥇", "🥈", "🥉"]
    for i, (name, score) in enumerate(finished):
        rank_icon = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{rank_icon} {name} — *{score}/{len(QUESTIONS)}*")

    lines.append(f"\n👥 Total students finished: *{len(finished)}*")

    bot.send_message(message.chat.id, "\n".join(lines), parse_mode="Markdown")


# ===========================================================================
# RENDER KEEP-ALIVE - Render's free Web Service tier requires an open port,
# but this bot only does Telegram polling (no web server of its own). This
# tiny Flask app gives Render something to see on that port. It runs in a
# background thread so it doesn't block the bot's polling loop.
# Locally in VS Code this just opens an extra unused port - harmless.
# ===========================================================================
web_app = Flask(__name__)


@web_app.route("/")
def home():
    return "ETquizhub bot is running."


def run_web():
    port = int(os.environ.get("PORT", 8080))  # Render sets PORT automatically
    web_app.run(host="0.0.0.0", port=port)


# ===========================================================================
# RUN THE BOT
# ===========================================================================
if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    print("Bot is running... (keep this window open, or later deploy to Render)")
    bot.infinity_polling()
