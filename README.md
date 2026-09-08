# ETquizhub 🎯
A timed academic tournament bot for Ethiopian students, running live on Telegram.

[Live bot: t.me/etquizhub_bot] · [Channel: t.me/et_quizhub]

---

## What it is

ETquizhub is a Telegram bot that runs weekly academic tournaments for Ethiopian students — Physics, Math, Chemistry, Biology, and Aptitude, one subject per day. Students register, pay a small entry fee (100 ETB), and compete solo against a personal timer answering curriculum-level questions. Top scorers get verified live and paid out from the pooled entry fees.

It's not a school project sitting in a folder somewhere. It's live right now, people are actually paying to join it, and I run the whole thing myself — from the code to the payment confirmations to calling the winners.

## Why I built it

Nobody told me to build this. No teacher, no program, no push from anyone — I just wanted to start something that could turn into a brand, and I wanted that brand to be about education mattering in this country. ETquizhub is the first, simplest version of that idea. It's small right now — a bot, a few hundred birr, a handful of students — but it's the start of something I want to keep building on.

## How it works

1. **Register** — student messages the bot, gives their name and the account they'll pay from
2. **Pay & confirm** — 100 ETB via Telebirr or bank transfer, I manually check my payment history and confirm
3. **Compete** — once confirmed, they start a timed quiz; one question at a time, tap to answer, no clutter
4. **Results** — instant score breakdown, plus a live leaderboard ranking everyone who's finished
5. **Payout** — I call the top scorers live on Telegram, ask a couple of fresh questions to make sure it's really them, then pay out

Why 100 ETB — kept it small on purpose. A big entry fee scares students off before they even try it. Small enough that trying it isn't a real decision, it's an impulse.

Why Physics goes first in the weekly rotation — it's my strongest subject and honestly my favorite one, so I wanted the tournament to open with it.

## Tech stack

- Python + `pyTelegramBotAPI` for the bot itself
- Flask, just to give the free hosting tier something to see on a port
- Deployed on Render, pushed from GitHub
- All tokens and payment info live in environment variables, never in the code itself
- Timezone math calculated straight from UTC, because Ethiopia doesn't do daylight saving and I didn't want the schedule breaking depending on what timezone the server happens to be in

## What actually went wrong building it

Took me about 3 weeks from the first line of code to having it live. The most annoying part was Render's free tier — it puts the whole bot to sleep after 15 minutes of no activity, so it would show "live" on the dashboard but just not respond on Telegram at all. Took me a while to figure out that was even the problem. I didn't give up on it, but I did think for a bit that barely anyone would notice or care if it just quietly didn't work. Decided to push through anyway, mainly because it wasn't costing me anything to keep trying — worst case I lose some evenings, not money.

## Built with Claude

I used Claude (Anthropic's AI) as a coding partner through most of this — debugging the Render issue, working through the timezone logic, cleaning up the flow. But the actual decisions — how the anti-cheat verification works, what the entry fee should be, how the weekly rotation is structured, whether to keep pushing after the Render bug — those were mine.

## What's next

This bot is step one. Next is taking the same idea to schools in person, and eventually building it into a full live academic competition. The goal was never just "build a bot" — it's to see if people actually want this, and build from there.

---
*Built and run by Ebawak Kibru. Reach me on email: ebawakkibru11@gmail.com
