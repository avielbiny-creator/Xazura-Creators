import asyncio
import json
import logging
import math
import re
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, ForceReply, Message

import db
import export
import keyboards as kb
import lolz
import texts
from config import ADMIN_IDS, DEFAULT_SETTINGS, LIST_SETTINGS, METHODS, SETTING_NAMES, ZERO_OK_SETTINGS
from utils import (
    admin_name, calc_amount, card_of, date, esc, fmt, level_info, min_total, money, month_bounds, month_start,
    parse_views, send_proofs, user_line,
)

router = Router()
router.message.filter(F.from_user.id.in_(ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(ADMIN_IDS))

ADMIN_MENU = "🛠 <b>Админ-панель</b>"
# Кнопки главного меню не съедаются вводом, а сбрасывают действие
NOT_MENU = F.text & ~F.text.in_(kb.MENU_TEXTS)
# В группе ответ на сообщение бота доходит даже с включённым privacy mode
REPLY = ForceReply()
# Больше ~100 кнопок Telegram в одном сообщении не принимает
LIST_LIMIT = 50


class Adm(StatesGroup):
    amount = State()
    reject = State()
    broadcast = State()
    find_user = State()
    adjust = State()
    offer = State()
    examples = State()
    credit = State()
    views = State()


@router.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    markup = kb.main_menu() if m.chat.type == "private" else None
    await m.answer("Действие отменено.", reply_markup=markup)


@router.message(Command("admin"))
async def admin_menu(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(ADMIN_MENU, reply_markup=kb.admin_menu_kb())


@router.callback_query(F.data == "adm:menu")
async def admin_menu_cb(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text(ADMIN_MENU, reply_markup=kb.admin_menu_kb())
    await cb.answer()


# ---------- админ-группа ----------

@router.message(Command("bind"), F.chat.type.in_({"group", "supergroup"}))
async def bind(m: Message):
    await db.set_setting("admin_chat", str(m.chat.id))
    await m.answer(
        "✅ Эта группа теперь админская: сюда будут приходить все новые заявки.\n"
        "Записи экрана и скриншоты бот присылает в личку тому, кто нажал кнопку, "
        "поэтому каждому админу нужно один раз написать боту /start."
    )


@router.message(Command("unbind"))
async def unbind(m: Message):
    await db.del_setting("admin_chat")
    await m.answer("Админ-группа отвязана. Заявки будут приходить админам в личку.")


# ---------- карточки заявок ----------

async def _refresh(bot: Bot, chat_id: int, message_id: int, kind: str, app_id: int):
    text, markup = await card_of(kind, app_id)
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup)
    except TelegramBadRequest:
        pass


async def _notify(bot: Bot, user_id: int, text: str):
    try:
        await bot.send_message(user_id, text)
    except TelegramAPIError as e:
        logging.warning("Не удалось написать креатору %s: %s", user_id, e)


async def _reward_referrer(bot: Bot, user_id: int, comments: int = 0, rub: int = 0):
    """Приглашённый получил выплату или зачисление: пригласившему его доля (и разовый бонус за первый раз)."""
    reward = await db.reward_referrer(
        user_id,
        bonus=int(db.num("ref_bonus")),
        comments=comments * int(db.num("ref_comments_pct")) // 100,
        rub=rub * int(db.num("ref_video_pct")) // 100,
        min_payout=int(db.num("ref_min_payout")),
    )
    if reward:
        user = await db.get_user(user_id)
        await _notify(bot, reward["referrer"], texts.ref_reward(user["full_name"] or "Креатор", reward))


def _parse(cb: CallbackQuery) -> tuple[str, str, int]:
    """adm:v:ok:12 → ('v', 'ok', 12)"""
    _, kind, action, app_id = cb.data.split(":")
    return kind, action, int(app_id)


@router.callback_query(F.data.startswith("adm:open:"))
async def open_app(cb: CallbackQuery):
    _, _, kind, app_id = cb.data.split(":")
    text, markup = await card_of(kind, int(app_id))
    await cb.message.answer(text, reply_markup=markup)
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:[vc]:proofs:\d+$"))
async def proofs(cb: CallbackQuery, bot: Bot):
    kind, _, app_id = _parse(cb)
    app = await (db.get_video_app(app_id) if kind == "v" else db.get_comment_app(app_id))
    code = f"#{app_id}" if kind == "v" else f"#К{app_id}"
    try:
        await bot.send_message(cb.from_user.id, f"📎 Пруфы по заявке <b>{code}</b>:")
    except TelegramAPIError:
        await cb.answer("Не могу написать тебе в личку. Открой бота и нажми /start, потом попробуй снова.",
                        show_alert=True)
        return
    await cb.answer("Отправляю в личку…")
    await send_proofs(bot, cb.from_user.id, json.loads(app["proofs"]))
    text, markup = await card_of(kind, app_id)
    await bot.send_message(cb.from_user.id, text, reply_markup=markup)


# ---------- видео: одобрить → выплачено ----------

@router.callback_query(F.data.regexp(r"^adm:v:ok:\d+$"))
async def video_ok(cb: CallbackQuery, bot: Bot):
    _, _, app_id = _parse(cb)
    if not (await db.get_video_app(app_id))["amount_rub"]:
        await cb.answer("Сначала нажми «👁 Просмотры» и введи просмотры со статистики.", show_alert=True)
        return
    if not await db.approve_video(app_id, admin_name(cb.from_user)):
        await cb.answer("Заявка уже обработана", show_alert=True)
        return
    await _refresh(bot, cb.message.chat.id, cb.message.message_id, "v", app_id)
    await cb.answer("👍 Одобрено, заявка в списке «К оплате»")
    await _notify(bot, (app := await db.get_video_app(app_id))["user_id"], texts.video_approved(app))


@router.callback_query(F.data.regexp(r"^adm:v:pay:\d+$"))
async def pay_ask(cb: CallbackQuery):
    await cb.message.edit_reply_markup(reply_markup=kb.admin_pay_confirm_kb(_parse(cb)[2]))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:v:back:\d+$"))
async def pay_back(cb: CallbackQuery):
    app_id = _parse(cb)[2]
    app = await db.get_video_app(app_id)
    await cb.message.edit_reply_markup(reply_markup=kb.admin_video_kb(app_id, app["status"], bool(app["note"])))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:v:payok:\d+$"))
async def pay_ok(cb: CallbackQuery, bot: Bot):
    app_id = _parse(cb)[2]
    app = await db.get_video_app(app_id)
    old_level = level_info((await db.get_user(app["user_id"]))["total_paid"])[0]
    if not await db.mark_video_paid(app_id, admin_name(cb.from_user)):
        await cb.answer("Заявка уже обработана", show_alert=True)
        return
    await _refresh(bot, cb.message.chat.id, cb.message.message_id, "v", app_id)
    await cb.answer("✅ Отмечено как выплачено")
    app = await db.get_video_app(app_id)
    await _notify(bot, app["user_id"], texts.video_paid(app))
    name, bonus, _ = level_info((await db.get_user(app["user_id"]))["total_paid"])
    if name != old_level:
        await _notify(bot, app["user_id"], texts.level_up(name, bonus))
    if not app["note"]:  # с бонусов топа и реферальных выплат доля не начисляется
        await _reward_referrer(bot, app["user_id"], rub=app["amount_rub"])


@router.callback_query(F.data.regexp(r"^adm:v:amt:\d+$"))
async def amount_ask(cb: CallbackQuery, state: FSMContext):
    app_id = _parse(cb)[2]
    await state.set_state(Adm.amount)
    await state.update_data(app_id=app_id, card=(cb.message.chat.id, cb.message.message_id))
    await cb.message.answer(f"✏️ Новая сумма в рублях для заявки #{app_id} (или /cancel):", reply_markup=REPLY)
    await cb.answer()


@router.message(Adm.amount, NOT_MENU)
async def amount_set(m: Message, state: FSMContext, bot: Bot):
    value = m.text.strip().replace(" ", "")
    if not value.isdigit():
        await m.answer("Нужно целое число рублей, например <code>350</code>", reply_markup=REPLY)
        return
    data = await state.get_data()
    await state.clear()
    if not await db.set_video_amount(data["app_id"], int(value)):
        await m.answer("Заявка уже закрыта, сумму не изменить.")
        return
    await _refresh(bot, *data["card"], "v", data["app_id"])
    await m.answer(f"✅ Сумма заявки #{data['app_id']}: {fmt(int(value))} ₽")


@router.callback_query(F.data.regexp(r"^adm:v:views:\d+$"))
async def views_ask(cb: CallbackQuery, state: FSMContext):
    app_id = _parse(cb)[2]
    await state.set_state(Adm.views)
    await state.update_data(app_id=app_id, card=(cb.message.chat.id, cb.message.message_id))
    await cb.message.answer(
        f"👁 Сколько просмотров засчитать по заявке #{app_id}? Сумма посчитается сама.\n"
        "Например <code>15400</code> или <code>15.4к</code> (или /cancel)",
        reply_markup=REPLY,
    )
    await cb.answer()


@router.message(Adm.views, NOT_MENU)
async def views_set(m: Message, state: FSMContext, bot: Bot):
    views = parse_views(m.text)
    if views is None:
        await m.answer("Не понял число. Например <code>15400</code> или <code>15.4к</code>", reply_markup=REPLY)
        return
    data = await state.get_data()
    await state.clear()
    app = await db.get_video_app(data["app_id"])
    if app["note"]:
        await m.answer("Это бонусная выплата без видео: сумму меняй кнопкой «✏️ Сумма».")
        return
    amount = calc_amount(views, app["rate"], app["bonus_pct"])
    if not await db.set_video_views(app["id"], views, amount):
        await m.answer("Заявка уже закрыта.")
        return
    await _refresh(bot, *data["card"], "v", app["id"])
    warn = f"\n⚠️ Меньше минималки ({fmt(min_total())} просм.)" if views < min_total() else ""
    await m.answer(f"✅ Заявка #{app['id']}: {fmt(views)} просм. → <b>{fmt(amount)} ₽</b>{warn}")


# ---------- комментарии: зачислить на баланс ----------

async def _credit(bot: Bot, app_id: int, credited: int, who: str) -> bool:
    if not await db.credit_comments(app_id, credited, who):
        return False
    app = await db.get_comment_app(app_id)
    user = await db.get_user(app["user_id"])
    await _notify(bot, app["user_id"], texts.comment_credited(app, user["comment_balance"]))
    await _reward_referrer(bot, app["user_id"], comments=credited)
    return True


@router.callback_query(F.data.regexp(r"^adm:c:ok:\d+$"))
async def comment_ok(cb: CallbackQuery, bot: Bot):
    app_id = _parse(cb)[2]
    app = await db.get_comment_app(app_id)
    if not await _credit(bot, app_id, app["count"], admin_name(cb.from_user)):
        await cb.answer("Заявка уже обработана", show_alert=True)
        return
    await _refresh(bot, cb.message.chat.id, cb.message.message_id, "c", app_id)
    await cb.answer(f"✅ Зачислено {app['count']} 💬")


@router.callback_query(F.data.regexp(r"^adm:c:part:\d+$"))
async def credit_ask(cb: CallbackQuery, state: FSMContext):
    app_id = _parse(cb)[2]
    app = await db.get_comment_app(app_id)
    await state.set_state(Adm.credit)
    await state.update_data(app_id=app_id, card=(cb.message.chat.id, cb.message.message_id))
    await cb.message.answer(
        f"✏️ Сколько комментариев засчитать по заявке #К{app_id}? (из {app['count']}, /cancel для отмены)",
        reply_markup=REPLY,
    )
    await cb.answer()


@router.message(Adm.credit, NOT_MENU)
async def credit_set(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    app = await db.get_comment_app(data["app_id"])
    value = m.text.strip()
    if not value.isdigit() or not 0 < int(value) <= app["count"]:
        await m.answer(f"Нужно число от 1 до {app['count']}. Если засчитывать нечего, отклони заявку.",
                       reply_markup=REPLY)
        return
    await state.clear()
    if not await _credit(bot, app["id"], int(value), admin_name(m.from_user)):
        await m.answer("Заявка уже обработана.")
        return
    await _refresh(bot, *data["card"], "c", app["id"])
    await m.answer(f"✅ Заявка #К{app['id']}: зачислено {value} из {app['count']} 💬")


# ---------- обмен: выдать аккаунт ----------

@router.callback_query(F.data.regexp(r"^adm:e:(done|cancel):\d+$"))
async def exchange_finish(cb: CallbackQuery, bot: Bot):
    _, action, ex_id = _parse(cb)
    done = action == "done"
    if not await db.finish_exchange(ex_id, done, admin_name(cb.from_user)):
        await cb.answer("Обмен уже обработан", show_alert=True)
        return
    await _refresh(bot, cb.message.chat.id, cb.message.message_id, "e", ex_id)
    ex = await db.get_exchange(ex_id)
    await _notify(bot, ex["user_id"], texts.exchange_done(ex) if done else texts.exchange_cancelled(ex))
    await cb.answer("✅ Креатор уведомлён" if done else "↩️ Комментарии возвращены")


# ---------- отказ (видео и комментарии) ----------

@router.callback_query(F.data.regexp(r"^adm:[vc]:rej:\d+$"))
async def reject_ask(cb: CallbackQuery, state: FSMContext):
    kind, _, app_id = _parse(cb)
    code = f"#{app_id}" if kind == "v" else f"#К{app_id}"
    await state.set_state(Adm.reject)
    await state.update_data(kind=kind, app_id=app_id, card=(cb.message.chat.id, cb.message.message_id))
    await cb.message.answer(
        f"❌ Причина отказа по заявке {code} (или <code>-</code> без причины, /cancel для отмены):",
        reply_markup=REPLY,
    )
    await cb.answer()


@router.message(Adm.reject, NOT_MENU)
async def reject_set(m: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    kind, app_id = data["kind"], data["app_id"]
    reason = "" if m.text.strip() == "-" else m.text.strip()[:500]
    who = admin_name(m.from_user)
    if kind == "v":
        ok = await db.reject_video(app_id, reason, who)
        app, code = await db.get_video_app(app_id), f"#{app_id}"
    else:
        ok = await db.reject_comments(app_id, reason, who)
        app, code = await db.get_comment_app(app_id), f"#К{app_id}"
    if not ok:
        await m.answer("Заявка уже обработана.")
        return
    await _refresh(bot, *data["card"], kind, app_id)
    await _notify(bot, app["user_id"], texts.rejected(code, reason))
    await m.answer(f"Заявка {code} отклонена, креатор уведомлён.")


# ---------- списки ----------

@router.callback_query(F.data.in_({"adm:list:v", "adm:list:c", "adm:list:e"}))
async def pending_list(cb: CallbackQuery):
    kind = cb.data[-1]
    if kind == "v":
        apps = await db.video_apps_by_status("pending")
        items = [(a["id"], f"#{a['id']} · {a['full_name'] or 'Креатор'} · @{a['profile'] or '?'}") for a in apps]
        title = "🎬 Видео на проверке"
    elif kind == "c":
        apps = await db.pending_comment_apps()
        items = [(a["id"], f"#К{a['id']} · {a['full_name'] or 'Креатор'} · {a['count']} 📸") for a in apps]
        title = "💬 Комментарии на проверке"
    else:
        apps = await db.pending_exchanges()
        items = [(a["id"], f"#О{a['id']} · {a['full_name'] or 'Креатор'} · {a['offer_title'][:30]}") for a in apps]
        title = "🎁 Ждут выдачи аккаунта"
    text = f"<b>{title}: {len(apps)}</b>" if apps else f"{title}: пусто 🎉"
    if len(items) > LIST_LIMIT:
        text += f"\nПоказаны первые {LIST_LIMIT}, остальные после их обработки."
    await cb.message.edit_text(text, reply_markup=kb.admin_apps_kb(kind, items[:LIST_LIMIT]))
    await cb.answer()


@router.callback_query(F.data == "adm:topay")
async def to_pay(cb: CallbackQuery):
    apps = await db.video_apps_by_status("approved")
    if not apps:
        await cb.message.edit_text("💸 К оплате: пусто 🎉", reply_markup=kb.back_kb("adm:menu", "⬅️ Меню"))
        return await cb.answer()
    total = sum(a["amount_rub"] for a in apps)
    text = f"💸 <b>К оплате: {len(apps)} на {fmt(total)} ₽</b>\n\n"
    shown = 0
    for a in apps[:LIST_LIMIT]:
        name = esc(a["full_name"] or "Креатор") + (f" (@{esc(a['username'])})" if a["username"] else "")
        block = (
            f"<b>#{a['id']}</b> · {name} · одобрено {date(a['approved_at'])}"
            + (f"\n{esc(a['note'])}" if a["note"] else "")
            + f"\n💰 <b>{money(a['amount_rub'], a['method'])}</b>"
            + f"\n{METHODS[a['method']]}: <code>{esc(a['requisites'])}</code>\n\n"
        )
        # Режем по целым заявкам: обрезка посреди HTML-тега ломает сообщение
        if len(text) + len(block) > 3700:
            break
        text += block
        shown += 1
    if shown < len(apps):
        text += f"…и ещё {len(apps) - shown}, они появятся здесь после выплаты первых.\n\n"
    text += "Нажми на заявку, чтобы отметить выплату."
    items = [(a["id"], f"💸 #{a['id']} · {money(a['amount_rub'])}") for a in apps[:LIST_LIMIT]]
    await cb.message.edit_text(text, reply_markup=kb.admin_apps_kb("v", items))
    await cb.answer()


# ---------- статистика ----------

@router.callback_query(F.data == "adm:stats")
async def stats(cb: CallbackQuery):
    s = await db.stats(month_start())
    text = (
        "📊 <b>Статистика</b>\n\n"
        f"👥 Креаторов: <b>{s['users']}</b> (в бане: {s['banned']})\n\n"
        "🎬 <b>Видео</b>\n"
        f"⏳ На проверке: <b>{s['pending']}</b>\n"
        f"👍 Ждут оплаты: <b>{s['approved']}</b> на {fmt(s['approved_sum'])} ₽\n"
        f"💸 Выплачено за месяц: <b>{fmt(s['month_sum'])} ₽</b> ({s['month']} выплат)\n"
        f"💰 Выплачено всего: <b>{fmt(s['paid_sum'])} ₽</b> ({s['paid']} выплат)\n\n"
        "💬 <b>Комментарии</b>\n"
        f"⏳ Заявок на проверке: <b>{s['c_pending']}</b>\n"
        f"✅ Зачислено за месяц: <b>{fmt(s['c_month'])}</b> 💬\n"
        f"💼 На балансах у креаторов: <b>{fmt(s['balance'])}</b> 💬\n\n"
        "🎁 <b>Обмен</b>\n"
        f"⏳ Ждут выдачи: <b>{s['e_pending']}</b>\n"
        f"✅ Выдано за месяц: <b>{s['e_month']}</b>, всего: {s['e_done']}"
    )
    await cb.message.edit_text(text, reply_markup=kb.back_kb("adm:menu", "⬅️ Меню"))
    await cb.answer()


# ---------- рассылка ----------

@router.callback_query(F.data == "adm:bc")
async def bc_ask(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.broadcast)
    await cb.message.answer(
        "📢 Отправь сообщение для рассылки (текст, фото, видео, что угодно). /cancel для отмены.", reply_markup=REPLY
    )
    await cb.answer()


@router.message(Adm.broadcast, ~F.text.in_(kb.MENU_TEXTS))
async def bc_preview(m: Message, state: FSMContext):
    await state.update_data(bc=(m.chat.id, m.message_id))
    await m.answer("👆 Так увидят креаторы. Разослать всем?", reply_markup=kb.admin_broadcast_kb())


@router.callback_query(Adm.broadcast, F.data == "adm:bc_no")
async def bc_no(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Рассылка отменена.")
    await cb.answer()


@router.callback_query(Adm.broadcast, F.data == "adm:bc_go")
async def bc_go(cb: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    await state.clear()
    chat_id, message_id = data["bc"]
    user_ids = await db.all_user_ids()
    await cb.message.edit_text(f"📢 Рассылаю на {len(user_ids)} креаторов…")
    await cb.answer()
    ok = 0
    for uid in user_ids:
        for _ in range(2):
            try:
                await bot.copy_message(uid, chat_id, message_id)
                ok += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except TelegramAPIError:
                break
        await asyncio.sleep(0.05)
    await cb.message.answer(f"✅ Рассылка завершена: доставлено {ok} из {len(user_ids)}")


# ---------- креаторы ----------

async def _user_card(user_id: int) -> str:
    user = await db.get_user(user_id)
    level, bonus, _ = level_info(user["total_paid"])
    lines = [
        f"👤 {user_line(user)}",
        f"🆔 <code>{user['id']}</code>",
        f"🏅 {level} (+{bonus}%)",
        f"💰 Выплачено: <b>{fmt(user['total_paid'])} ₽</b> · выплат: {await db.paid_count(user_id)}",
        f"💬 Баланс комментариев: <b>{fmt(user['comment_balance'])}</b> · "
        f"получено аккаунтов: {await db.done_exchange_count(user_id)}",
        f"📅 В боте с {date(user['created_at'])}",
    ]
    if pending := await db.pending_video_of(user_id):
        lines.append(f"⏳ Заявка за видео #{pending['id']} на проверке")
    if pending := await db.pending_comment_of(user_id):
        lines.append(f"⏳ Заявка за комментарии #К{pending['id']} на проверке")
    if user["banned"]:
        lines.append("🚫 <b>Забанен</b>")
    return "\n".join(lines)


@router.callback_query(F.data == "adm:user")
async def user_ask(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.find_user)
    await cb.message.answer("👤 Отправь ID или @username креатора (/cancel для отмены):", reply_markup=REPLY)
    await cb.answer()


@router.message(Adm.find_user, NOT_MENU)
async def user_found(m: Message, state: FSMContext):
    user = await db.find_user(m.text)
    if not user:
        await m.answer("Не нашёл. Креатор должен хотя бы раз запустить бота. Попробуй ещё раз или /cancel",
                       reply_markup=REPLY)
        return
    await state.clear()
    await m.answer(await _user_card(user["id"]), reply_markup=kb.admin_user_kb(user))


@router.callback_query(F.data.startswith("adm:ban:") | F.data.startswith("adm:unban:"))
async def ban_toggle(cb: CallbackQuery):
    action, uid = cb.data.split(":")[1], int(cb.data.split(":")[2])
    await db.set_banned(uid, action == "ban")
    user = await db.get_user(uid)
    await cb.message.edit_text(await _user_card(uid), reply_markup=kb.admin_user_kb(user))
    await cb.answer("Готово")


@router.callback_query(F.data.startswith("adm:adj:") | F.data.startswith("adm:bal:"))
async def adjust_ask(cb: CallbackQuery, state: FSMContext):
    field = cb.data.split(":")[1]
    await state.set_state(Adm.adjust)
    await state.update_data(uid=int(cb.data.split(":")[2]), field=field)
    if field == "adj":
        text = ("✏️ На сколько изменить сумму «выплачено»? Например <code>500</code> или <code>-200</code>.\n"
                "От неё зависит уровень. Так можно учесть бонус топа или выплату мимо бота.")
    else:
        text = "✏️ На сколько изменить баланс комментариев? Например <code>50</code> или <code>-20</code>."
    await cb.message.answer(text + " /cancel для отмены.", reply_markup=REPLY)
    await cb.answer()


@router.message(Adm.adjust, NOT_MENU)
async def adjust_set(m: Message, state: FSMContext):
    try:
        delta = int(m.text.strip().replace(" ", ""))
    except ValueError:
        await m.answer("Нужно целое число, например <code>500</code> или <code>-200</code>", reply_markup=REPLY)
        return
    data = await state.get_data()
    uid = data["uid"]
    await state.clear()
    if data["field"] == "adj":
        await db.add_total_paid(uid, delta)
    else:
        await db.add_comment_balance(uid, delta)
    user = await db.get_user(uid)
    await m.answer(await _user_card(uid), reply_markup=kb.admin_user_kb(user))


# ---------- прайс аккаунтов за комментарии ----------

OFFER_FORMAT = (
    "<code>80 ID 41 🇮🇩 Индонезия [+62]</code>\n"
    "комментарии · код страны · потолок закупки на LOLZ в ₽ · название\n\n"
    "Без кода и потолка (<code>80 🇮🇩 Индонезия</code>) аккаунт будешь выдавать вручную."
)


async def _offers_view():
    offers = await db.list_offers()
    lines = ["🏷 <b>Прайс обмена комментариев на аккаунты</b>", ""]
    for o in offers:
        auto = f"⚡️ авто: {o['code']} до {fmt(o['max_price'])} ₽" if o["code"] else "✋ вручную"
        lines.append(f"⚪️ {o['comments']} 💬 · {esc(o['title'])}\n      {auto}")
    if not offers:
        lines.append("Пусто: обменять комментарии сейчас не на что.")
    if not lolz.client:
        lines += ["", "⚠️ В .env нет LOLZ_TOKEN: всё выдаётся вручную."]
    lines += ["", "Нажми 🗑, чтобы убрать позицию. Чтобы поменять цену, удали и добавь заново."]
    return "\n".join(lines), kb.admin_offers_kb(offers)


@router.callback_query(F.data == "adm:offers")
async def offers(cb: CallbackQuery):
    text, markup = await _offers_view()
    await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer()


@router.callback_query(F.data == "adm:offer_add")
async def offer_ask(cb: CallbackQuery, state: FSMContext):
    await state.set_state(Adm.offer)
    await cb.message.answer(f"➕ Отправь позицию одной строкой:\n\n{OFFER_FORMAT}\n\n/cancel для отмены", reply_markup=REPLY)
    await cb.answer()


@router.message(Adm.offer, NOT_MENU)
async def offer_add(m: Message, state: FSMContext):
    parts = m.text.split()
    code = max_price = None
    if len(parts) >= 4 and re.fullmatch(r"[A-Za-z]{2}", parts[1]) and re.fullmatch(r"\d+([.,]\d+)?", parts[2]):
        code, max_price = parts[1].upper(), float(parts[2].replace(",", "."))
        parts = [parts[0]] + parts[3:]
    if len(parts) < 2 or not parts[0].isdigit() or int(parts[0]) <= 0:
        await m.answer(f"Не понял. Формат:\n\n{OFFER_FORMAT}", reply_markup=REPLY)
        return
    await state.clear()
    await db.add_offer(int(parts[0]), " ".join(parts[1:])[:100], code, max_price)
    text, markup = await _offers_view()
    await m.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("adm:odel:"))
async def offer_delete(cb: CallbackQuery):
    await db.delete_offer(int(cb.data.split(":")[2]))
    text, markup = await _offers_view()
    await cb.message.edit_text(text, reply_markup=markup)
    await cb.answer("Удалено")


# ---------- примеры комментариев ----------

@router.callback_query(F.data == "adm:examples")
async def examples_show(cb: CallbackQuery, state: FSMContext):
    current = db.get_setting("comment_examples")
    await state.set_state(Adm.examples)
    await cb.message.answer(
        "📝 <b>Примеры комментариев</b>\n\n"
        + (f"Сейчас:\n{esc(current)}\n\n" if current else "Сейчас примеров нет, кнопка у креаторов скрыта.\n\n")
        + "Отправь новый текст примеров целиком (заменит старый), <code>-</code> чтобы убрать, /cancel для отмены.",
        reply_markup=REPLY,
    )
    await cb.answer()


@router.message(Adm.examples, NOT_MENU)
async def examples_set(m: Message, state: FSMContext):
    await state.clear()
    if m.text.strip() == "-":
        await db.del_setting("comment_examples")
        await m.answer("✅ Примеры убраны.")
        return
    await db.set_setting("comment_examples", m.text.strip()[:3500])
    await m.answer("✅ Примеры сохранены. Креаторы увидят кнопку «📝 Примеры комментариев» в разделе комментариев.")


# ---------- выгрузка в Excel ----------

@router.callback_query(F.data == "adm:export")
async def export_menu(cb: CallbackQuery):
    await cb.message.edit_text("📥 <b>Выгрузка в Excel</b>\n\nЗа какой период?", reply_markup=kb.admin_export_kb())
    await cb.answer()


@router.callback_query(F.data.startswith("adm:exp:"))
async def export_file(cb: CallbackQuery):
    period = cb.data.split(":")[2]
    if period == "all":
        start, end, label = 0, int(time.time()) + 1, "всё время"
    else:
        start, end, label = month_bounds(0 if period == "cur" else -1)
    await cb.answer("Собираю файл…")
    data = await export.build(start, end)
    name = f"xazura_{label.replace(' ', '_')}.xlsx"
    await cb.message.answer_document(
        BufferedInputFile(data, name), caption=f"📥 Выплаты, зачисления и выданные аккаунты: {label}"
    )


# ---------- настройки ----------

def _settings_text() -> str:
    values = db.all_settings()
    lines = ["⚙️ <b>Настройки</b>", ""]
    for key in DEFAULT_SETTINGS:
        lines.append(f"{SETTING_NAMES[key]}:\n<code>{key}</code> = <b>{esc(values[key])}</b>\n")
    lines.append(
        "Изменить: <code>/set ключ значение</code>\n"
        "Например: <code>/set rate 160</code> или <code>/set top_rewards 1500,700,300</code>"
    )
    return "\n".join(lines)


@router.callback_query(F.data == "adm:settings")
async def settings(cb: CallbackQuery):
    await cb.message.edit_text(_settings_text(), reply_markup=kb.back_kb("adm:menu", "⬅️ Меню"))
    await cb.answer()


@router.message(Command("set"))
async def set_setting(m: Message):
    parts = m.text.split(maxsplit=2)
    if len(parts) < 3 or parts[1] not in DEFAULT_SETTINGS:
        await m.answer(_settings_text())
        return
    key, value = parts[1], parts[2].strip()
    if key in LIST_SETTINGS:
        items = [x.strip() for x in value.replace(" ", "").split(",")]
        if not all(x.isdigit() for x in items):
            await m.answer("Нужны целые числа через запятую, например <code>1000,500,250</code>")
            return
        value = ",".join(items)
    else:
        try:
            number = float(value.replace(",", "."))
            if not math.isfinite(number) or number < 0 or (number == 0 and key not in ZERO_OK_SETTINGS):
                raise ValueError
        except ValueError:
            await m.answer("Нужно положительное число." + (" Можно 0, чтобы выключить." if key in ZERO_OK_SETTINGS else ""))
            return
        value = value.replace(",", ".")
    await db.set_setting(key, value)
    await m.answer(f"✅ {SETTING_NAMES[key]}: <b>{esc(value)}</b>")
