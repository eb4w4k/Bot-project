# ETquizhub Bot 🎓

A Telegram bot that gives Ethiopian high-school students (grades 9–12) daily, timed, subject-by-subject exam practice, and handles registration for the in-person **ETquizhub Live** exam.

👉 **Try it:** [t.me/ETquizhub_bot](https://t.me/ETquizhub_bot)

Built by a Grade 12 graduate in Ethiopia to make serious exam preparation accessible, structured, and competitive in a healthy way.

---

## What it does

- **Two grade tiers.** Tier A (Grade 9–10) and Tier B (Grade 11–12). Every student gets questions at their own level.
- **Daily Practice.** One subject per day, 25 questions per quiz, a personal 20-minute timer, and a limited number of attempts per day. After the last answer, the student gets a full breakdown of every question.
- **Leaderboard.** The admin can see the day's top scorers per tier and subject.
- **ETquizhub Live registration.** A separate package for the in-person school exam. Confirmed students receive a numbered ticket (for example `ETQL-B-001`) as proof of registration.
- **Packages.** Practice, Live, or a Bundle of both with a single payment.
- **Admin controls.** Payment confirmation, leaderboard, and a status overview, all from Telegram.

## Weekly schedule

| Day | Subject |
|---|---|
| Monday | Physics |
| Tuesday | Mathematics |
| Wednesday | Chemistry |
| Thursday | Biology |
| Friday | Aptitude / General (Logical) Reasoning |
| Saturday | English |

Each subject is open all day on its day (Ethiopian time).

## How it works

**Student**

1. `/start` → full name → name on the paying account → tier → package
2. Pay, then send `/paid`
3. After confirmation, `/quiz` on a scheduled day

**Admin**

1. Receives a payment notification with the student's details
2. Checks the payment, then runs `/confirm <telegram_id>`
3. The student is unlocked and, for Live, receives a ticket automatically

Other commands: `/leaderboard`, `/status`, `/myticket`, `/timeleft`, `/help`.

## Tech stack

- **Python** with [pyTelegramBotAPI](https://github.com/eternnoir/pyTelegramBotAPI)
- **Neon** (serverless PostgreSQL) for students, payments, tickets and quiz attempts, so a redeploy never loses data
- **Flask** (tiny web page so the free web service on **Render** stays reachable)
- Hosted on Render's free tier, kept awake with an uptime pinger

## Configuration

Secrets are never stored in the code. Set these as environment variables on the host:

| Variable | Purpose |
|---|---|
| `BOT_TOKEN` | Telegram bot token from BotFather |
| `ADMIN_ID` | Telegram user ID of the admin |
| `DATABASE_URL` | Neon PostgreSQL connection string |

Three switches at the top of `etquizhub_bot.py` control the system:

| Switch | Effect |
|---|---|
| `BOT_ACTIVE` | `False` shows every student an "under update" message |
| `LIVE_REGISTRATION_OPEN` | `False` keeps Live registration closed (students see it as locked) |
| `REGISTRATION_PAUSED` | `True` stops new registrations and purchases |

Database tables are created automatically on first start.

## Deploy

1. Create a Neon project and copy its connection string into `DATABASE_URL`.
2. Create a Render **Web Service** from this repository, with the three environment variables above.
3. Build command: `pip install -r requirements.txt`. Start command: `python etquizhub_bot.py`.

## Updating the questions

Questions live in a separate file, `questions.py`, so the bot logic never has to change when the questions do. Each week a fresh set of 25 questions per subject and tier is drawn at random from larger question banks (100 questions each), spread proportionally across units, and `questions.py` is replaced. The banks themselves and the drawing script are kept locally and are not part of this repository.

## Project files

| File | Purpose |
|---|---|
| `etquizhub_bot.py` | The bot: registration, payments, tickets, quizzes, admin commands |
| `questions.py` | The current week's questions, per subject and tier |
| `requirements.txt` | Python dependencies |

## Status

Live and in active development. Practice is open; Live registration opens when an exam date is announced.

---

*Made in Addis Ababa, Ethiopia. 🇪🇹*
