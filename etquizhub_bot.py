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
12. After the 18:00 cutoff, YOU manually check the leaderboard, call the
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
TOURNAMENT_PAUSED = True

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
QUIZ_TIME_LIMIT_MINUTES = 25

# Last moment (Ethiopian local time, 24h) a student may START the quiz.
# No new starts allowed at or after this time. Students already mid-quiz
# are NOT affected - they keep their full QUIZ_TIME_LIMIT_MINUTES.
QUIZ_CUTOFF_HOUR = 12
QUIZ_CUTOFF_MINUTE = 0

# List every payment method students can use. Edit these lines with your
# real account details.
PAYMENT_METHODS = (
    "• Telebirr: 0945065300 - (Ebawak Kibru)\n"
    "• CBE: 1000712174688 - (Natnael Mosisa)\n"
   
)

bot = telebot.TeleBot(BOT_TOKEN)

# ===========================================================================
# TIME HELPERS - Ethiopia is fixed UTC+3, no daylight saving
# ===========================================================================
EAT_OFFSET = timedelta(hours=3)
QUIZ_TIME_LIMIT = timedelta(minutes=QUIZ_TIME_LIMIT_MINUTES)
QUIZ_CUTOFF_TIME = dtime((QUIZ_CUTOFF_HOUR + 6) % 24, QUIZ_CUTOFF_MINUTE)


def now_eat():
    """Current date+time in Ethiopia (EAT), computed from UTC so it's
    correct no matter what timezone the machine running this bot is set to."""
    return datetime.utcnow() + EAT_OFFSET


def cutoff_has_passed():
    """True once we're at or past today's cutoff clock time in Ethiopia."""
    return now_eat().time() >= QUIZ_CUTOFF_TIME


WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def next_scheduled_day():
    """Finds the next tournament, starting from today. Registration no
    longer needs to happen on the same day as the quiz itself - a student
    can register on Saturday for Monday's round.
    Returns (weekday_index, subject_name):
      - If today has a subject AND today's cutoff hasn't passed yet ->
        returns today.
      - Otherwise, searches forward (tomorrow, then the day after, etc.)
        for the next day that has a subject assigned in SCHEDULE.
      - Returns (None, None) if SCHEDULE has no subjects at all.
    """
    today = now_eat().weekday()
    today_subject = SCHEDULE.get(today)
    if today_subject and not cutoff_has_passed():
        return today, today_subject

    for offset in range(1, 8):
        day = (today + offset) % 7
        subject = SCHEDULE.get(day)
        if subject:
            return day, subject

    return None, None


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
        "text": "Two point charges, +3.0 μC and -6.0 μC, are separated by 0.30m in air. Calculate the magnitude of the electrostatic force between them.(k=9.0x10⁹ N•m²/C²)",
        "choices": ["0.6N", "1.2N", "1.8N", "3.6N"],
        "correct": "1.8N",
    },
    {
        "text": "Three coplanar forces act on a point: F1 = 40 N at 0°, F2 = 30 N at 90°, F3 = 50 N at 210° (measured from the positive x-axis). Find the magnitude of the resultant force.",
        "choices": ["3.3N", "6.0N", "8.6N", "10.0N"],
        "correct": "6.0N",
    },
    {
        "text": "A long straight wire carries a current of 15 A. Calculate the magnetic field strength at a point 0.050 m from the wire.(μ0 = 4π x 10⁻⁷ T•m/A)",
        "choices": ["3.0x10⁻⁵ T", "6.0x10⁻⁵ T", "1.2x10⁻⁴ T", "6.0x10⁻⁴ T"],
        "correct": "6.0x10⁻⁵ T",
    },
    {
        "text": "A car starts from rest and accelerates uniformly, covering 100 m in the first 5.0 s. It then continues at the velocity it reached for a further 10 s at constant speed. Find the TOTAL distance covered in the full 15 s.",
        "choices": ["300m", "400m", "500m", "600m"],
        "correct": "500m",
    },
    {
        "text": "A converging lens has a focal length of 15cm. An object is placed 20cm from the lens. Find the image distance and the magnification.",
        "choices": ["di = 60cm, m = -3 (real, inverted)", "di = 12cm, m = -0.6 (real, inverted)", "di = 60cm, m = +3 (virtual, upright)", "di = 35cm, m = -1.75"],
        "correct": "di = 60 cm, m = -3 (real, inverted)",
    },
    {
        "text": "A steel wire 2.0m long with cross-sectional area 1.0x10⁻⁶m² stretches by 1.0mm under a load of 200N. Calculate the Young's modulus of the wire material.",
        "choices": ["2.0x10⁸Pa", "4.0x10¹⁰Pa", "4.0x10¹¹Pa", "2.0x10¹¹Pa"],
        "correct": "4.0x10¹¹Pa",
    },
    {
        "text": "A 50N picture frame hangs from two strings attached to the ceiling at the same point on the frame. One string makes 30° with the ceiling, the other makes 45° with the ceiling, on opposite sides. Find the tension in each string.",
        "choices": ["T(30°) = 44.8N, T(45°) = 36.6N", "T(30°) = 36.6N, T(45°) = 44.8N", "T(30°) = 25.0N, T(45°) = 35.4N", "T(30°) = 50.0N, T(45°) = 50.0N"],
        "correct": "T(30°) = 36.6N, T(45°) = 44.8N",
    },
    {
        "text": "A 4.0Ω resistor is connected in series with a 6.0Ω resistor. This combination is connected in parallel with a 12.0Ω resistor, and the whole network is connected to a 24V battery. Find the total current drawn from the battery.",
        "choices": ["2.0A", "2.4A", "4.4A", "6.0A"],
        "correct": "4.4A",
    },
    {
        "text": "A ball is thrown vertically upward with an initial velocity of 30m/s. Using g = 10m/s², find the maximum height reached and the total time to return to the starting point.",
        "choices": ["h = 45m, t = 6sec", "h = 90m, t = 6sec", "h = 45m, t = 3sec", "h = 30m, t = 3sec"],
        "correct": "h = 45m, t = 6sec",
    },
    {
        "text": "A straight wire of length 0.50m carrying a current of 8.0A is placed perpendicular to a uniform magnetic field of 0.25T. Calculate the force on the wire.",
        "choices": ["0.5N", "1.0N", "2.0N", "4.0N"],
        "correct": "1.0N",
    },
    {
        "text": "A uniform horizontal beam of weight 200N and length 6.0m is hinged at one end. A vertical cable is attached 4.0m from the hinge, and a 150N load hangs from the far end (6.0m from the hinge). Calculate the tension in the cable needed to keep the beam horizontal.",
        "choices": ["275N", "300N", "375N", "450N"],
        "correct": "375N",
    },
    {
        "text": "A ray of light travels from air (n = 1.00) into glass (n = 1.50) striking the surface at an angle of incidence of 40°. Calculate the angle of refraction.",
        "choices": ["20.7°", "25.4°", "30.0°", "60.0°"],
        "correct": "25.4°",
    },
    {
        "text": "A force of 120N acts at 35° above the horizontal. Calculate its horizontal and vertical components.",
        "choices": ["Fx = 68.8N, Fy = 98.3N", "Fx = 98.3N, Fy = 68.8N", "Fx = 120N, Fy = 0N", "Fx = 84.9N, Fy = 84.9N"],
        "correct": "Fx = 98.3N, Fy = 68.8N",
   },
   {
        "text": "An electric heater has a resistance of 25Ω and operates on a 220V supply. Calculate the power dissipated and the energy consumed in 2.0 hours.",
        "choices": ["P = 1936W, E = 3.87kWh", "P = 968W, E = 1.94kWh", "P = 8.8W, E = 17.6kWh", "P = 2200W, E = 2.2kWh"],
        "correct": "P = 1936W, E = 3.87kWh",
   },
   {
        "text": "Car A travels east at 25m/s. Car B travels west at 15m/s on the same straight road. Calculate the velocity of car A relative to car B.",
        "choices": ["10m/s east", "10m/s west", "40m/s east", "40m/s west"],
        "correct": "40m/s east",
   },
   {
        "text": "A copper rod of cross-sectional area 2.0x10⁻⁴m² supports a hanging load, producing a stress of 5.0x10⁷Pa in the rod. Calculate the mass of the load. (g = 10m/s²)",
        "choices": ["m = 100kg", "m = 250kg", "m = 500kg", "m = 1000kg"],
        "correct": "m = 1000kg",
   },
   {
        "text": "A concave mirror has a focal length of 20cm. An object isplaced 30cm in front of the mirror. Find the image distance and state the nature of the image.",
        "choices": ["di = 60cm, real and inverted", "di = 60cm, virtual and upright", "di = 12cm, real and inverted", "di = 50cm, reall and upright"],
        "correct": "di = 60cm, real and inverted",
   },
   {
        "text": "Two long parallel wires carry currents of 10A and 15A in the same direction, separated by 0.20m. Calculate the force per unit length between the wires and state whether it is attractive or repulsive.",
        "choices": ["7.5x10⁻⁵ N/m, repulsive", "1.5x10⁻⁴ N/m, attractive", "3.0x10⁻⁴N/m, attractive", "1.5x10⁻⁴N/m, repulsive"],
        "correct": "1.5x10⁻⁴N/m, attractive",
   },
   {
        "text": "Calculate the magnitude of the electric field at a point 0.10m from a point charge of 5.0μC. (k = 9.0x10⁹N•m²/C²)",
        "choices": ["4.5x10⁴ N/C", "4.5x10⁵ N/C", "4.5x10⁶ N/C", "4.5x10³ N/C"],
        "correct": "4.5x10⁶ N/C",
   },
   {
        "text": "Vector A has magnitude 8.0 units at 60°, and vector B has magnitude 5.0 units at 150°. Calculate the magnitude of A - B.",
        "choices": ["|A - B| = 3.0 units", "|A - B| = 9.4 units", "|A - B| = 5.7 units", "|A - B| = 13.0 units"],
        "correct": "|A - B| = 9.4 units",
   },
   {
        "text": "A radio wave has a frequency of 100MHz. Calculate its wavelength as it travels through air. (c = 3.0x10⁸m/s)",
        "choices": ["λ = 0.3m", "λ = 3.0m", "λ = 30m", "λ = 300m"],
        "correct": "λ = 3.0m",
   },
   {
        "text": "A stone is dropped from rest from a height of 80m. Ignoring air resistance, calculate the time it takes to reach the ground and its velocity just before impact. (g = 10m/s²)",
        "choices": ["t = 2.0sec, v = 20m/s", "t = 4.0sec, v = 40m/s", "t = 8.0sec, v = 80m/s", "t = 4.0sec, v = 20m/s"],
        "correct": "t = 4.0sec, v = 40m/s",
   },
   {
        "text": "A massless horizontal beam is hinged to a wall. A signboard of weight 80N hangs from the free end. A cable also attached at the free end runs back to the wall, making an angle of 37° with the beam (sin37° = 0.60, cos37° = 0.80). Calculate the tension in the cable and the horizontal reaction force the wall exerts on the beam.",
        "choices": ["T = 48N, H = 64N", "T = 106.7N, H = 133.30N", "T = 80N, H = 60N", "T = 133.3N, H = 106.7N"],
        "correct": "T = 133.3N, H = 106.7N",
   },
   {
        "text": "A proton (q = 1.6x10⁻¹⁹C) moves at 2.0x10⁶m/s perpendicular to a magnetic field of 0.50T. Calculate the magnetic force on the proton.",
        "choices": ["F = 8.0x10⁻¹⁴N", "F = 1.6x10⁻¹³N", "F = 1.6x10⁻¹²N", "F = 3.2x10⁻¹³ N"],
        "correct": "F = 1.6x10⁻¹³N",
   },
   {
        "text": "A train travelling at 30m/s decelerates uniformly and comes to rest after covering 250m. Calculate the deceleration and the time taken to stop.",
        "choices": ["a = 0.9m/s², t = 33.3s", "a = 3.6m/s², t = 8.3s", "a = 1.2m/s², t = 25s", "a = 1.8m/s², t = 16.7s"],
        "correct": "a = 1.8m/s², t = 16.7s",
   },
]
 

# ===========================================================================
# STEP 1: REGISTRATION - name
# ===========================================================================
@bot.message_handler(commands=["start"])
def start(message):
    user_id = message.from_user.id

    if TOURNAMENT_PAUSED:
        bot.send_message(
            message.chat.id,
            "🚫 *Registration is currently paused.*\n\n"
            "Check the channel for when the next tournament opens.",
            parse_mode="Markdown",
        )
        return

    weekday, subject = next_scheduled_day()
    if subject is None:
        bot.send_message(
            message.chat.id,
            "🚫 *No tournament is currently scheduled.*\n\n"
            "Check the channel for updates.",
            parse_mode="Markdown",
        )
        return

    when_text = "today" if weekday == now_eat().weekday() else WEEKDAY_NAMES[weekday]

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
        f"You're registering for *{subject}* ({when_text}).\n\n"
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
        f"{QUIZ_CUTOFF_HOUR:02d}:{QUIZ_CUTOFF_MINUTE:02d} (Ethiopian time) on the day of the "
        f"tournament. Once you start, you'll have {QUIZ_TIME_LIMIT_MINUTES} minutes to finish.",
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
            f"{QUIZ_CUTOFF_HOUR:02d}:{QUIZ_CUTOFF_MINUTE:02d} Ethiopian time on the day of "
            f"the tournament).",
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

    # Automatic day-gate: even a confirmed, paid student can't start the
    # quiz on a day with no tournament scheduled (e.g. registered Saturday,
    # tournament is Monday - /quiz stays locked until Monday, no manual
    # switch needed from the admin).
    if SCHEDULE.get(now_eat().weekday()) is None:
        weekday, subject = next_scheduled_day()
        if subject:
            when_text = "today" if weekday == now_eat().weekday() else WEEKDAY_NAMES[weekday]
            bot.send_message(
                message.chat.id,
                f"📅 The quiz isn't open yet — *{subject}* opens {when_text}. "
                "You're registered and confirmed, just come back then and send /quiz.",
                parse_mode="Markdown",
            )
        else:
            bot.send_message(
                message.chat.id,
                "📅 No tournament is currently scheduled. Check the channel for updates.",
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
