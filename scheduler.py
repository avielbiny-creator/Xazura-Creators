"""Фоновые задачи: бонусы топа в начале месяца, напоминания админам и креаторам."""
import asyncio
import logging
import time
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramRetryAfter

import db
import keyboards as kb
import texts
from config import ADMIN_IDS, ADMIN_REMIND_HOURS
from utils import admin_targets, month_bounds, top_rewards

CHECK_EVERY = 30 * 60
# Креаторам пишем только днём
NUDGE_HOURS = range(11, 21)


async def _send(bot: Bot, chat_id: int, text: str, **kwargs) -> bool:
    for _ in range(2):
        try:
            await bot.send_message(chat_id, text, **kwargs)
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
        except TelegramAPIError:
            return False
    return False


async def award_top(bot: Bot):
    """Раз в месяц: бонусы за 1–3 место прошлого месяца попадают в «К оплате», всем рассылаем итоги."""
    start, end, label = month_bounds(-1)
    if db.get_setting("top_awarded") == label:
        return
    await db.set_setting("top_awarded", label)
    rows = await db.top_between(start, end)
    winners = []
    for place, (row, amount) in enumerate(zip(rows, top_rewards()), 1):
        if amount <= 0:
            continue
        app_id = await db.create_bonus_payout(row["user_id"], amount, f"🏆 {place} место в топе за {label}")
        winners.append((place, row, amount))
        await _send(bot, row["user_id"], texts.top_winner(place, label, amount))
        logging.info("Бонус топа: %s место, креатор %s, %s ₽, заявка #%s", place, row["user_id"], amount, app_id)
    if not winners:
        return
    for chat_id in admin_targets():
        await _send(bot, chat_id, texts.top_awarded_admin(label, winners))
    summary = texts.top_results(label, winners)
    for uid in await db.all_user_ids():
        await _send(bot, uid, summary)
        await asyncio.sleep(0.05)


async def remind_admins(bot: Bot):
    """Если заявки висят без проверки дольше суток, напоминаем, но не чаще раза в 12 часов."""
    now = int(time.time())
    if now - int(db.get_setting("admin_reminded_at") or 0) < 12 * 3600:
        return
    counts = await db.stale_counts(now - ADMIN_REMIND_HOURS * 3600)
    if not any(counts.values()):
        return
    await db.set_setting("admin_reminded_at", str(now))
    for chat_id in admin_targets():
        await _send(bot, chat_id, texts.admin_reminder(counts, ADMIN_REMIND_HOURS))


async def nudge_creators(bot: Bot):
    days = int(db.num("nudge_days"))
    if days <= 0 or datetime.now().hour not in NUDGE_HOURS:
        return
    for uid in await db.users_to_nudge(int(time.time()) - days * 86400):
        if uid in ADMIN_IDS:
            continue
        await db.mark_nudged(uid)
        await _send(bot, uid, texts.nudge(days), reply_markup=kb.apply_kb())
        await asyncio.sleep(0.05)


async def run(bot: Bot):
    await asyncio.sleep(60)
    while True:
        for job in (award_top, remind_admins, nudge_creators):
            try:
                await job(bot)
            except Exception:
                logging.exception("Ошибка в фоновой задаче %s", job.__name__)
        await asyncio.sleep(CHECK_EVERY)
