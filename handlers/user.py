import time

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import db
import keyboards as kb
import texts
from handlers import comments, video
from keyboards import Btn
from utils import month_bounds, send_start_banner

REF_WINDOW = 10 * 60

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")
# Подключается последним: ловит всё, что не поймали остальные
fallback_router = Router()


async def _drop_kb(cb: CallbackQuery):
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


@router.message(CommandStart())
async def start(m: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    await send_start_banner(m, texts.welcome(m.from_user.first_name), kb.main_menu())
    # Ссылка-приглашение вида t.me/бот?start=ref_123 засчитывается только новичку (первые 10 минут в боте)
    if command.args and command.args.startswith("ref_") and command.args[4:].isdigit():
        referrer = int(command.args[4:])
        if await db.set_referrer(m.from_user.id, referrer, int(time.time()) - REF_WINDOW):
            try:
                await m.bot.send_message(referrer, texts.ref_joined(m.from_user.full_name))
            except TelegramAPIError:
                pass


@router.message(Command("id"))
async def my_id(m: Message):
    await m.answer(f"🆔 Твой ID: <code>{m.from_user.id}</code>")


@router.message(Command("cancel"))
async def cancel_cmd(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Действие отменено.", reply_markup=kb.main_menu())


@router.callback_query(F.data == "flow:cancel")
async def cancel_flow(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _drop_kb(cb)
    await cb.message.answer("❌ Отменено.", reply_markup=kb.main_menu())
    await cb.answer()


# ---------- разделы меню (работают из любого состояния и сбрасывают его) ----------

@router.message(F.text == Btn.APPLY)
async def apply(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(texts.APPLY, reply_markup=kb.apply_kb())


@router.callback_query(F.data.in_({"apply:video", "apply:comments"}))
async def apply_pick(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await _drop_kb(cb)
    if cb.data == "apply:comments":
        await comments.begin(cb.message, state, cb.from_user.id)
    else:
        await video.begin(cb.message, state, cb.from_user.id)
    await cb.answer()


@router.message(F.text == Btn.EXCHANGE)
async def exchange(m: Message, state: FSMContext):
    await state.clear()
    await comments.show_exchange(m, m.from_user.id)


@router.message(F.text == Btn.PROFILE)
async def profile(m: Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    await m.answer(texts.profile_text(await db.get_user(uid), await db.paid_count(uid), await db.done_exchange_count(uid)))


@router.message(F.text == Btn.REFERRAL)
async def referral(m: Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    link = f"https://t.me/{(await m.bot.me()).username}?start=ref_{uid}"
    text = texts.referral_text(await db.get_user(uid), link, await db.referral_stats(uid), await db.referrals(uid))
    await m.answer(text, reply_markup=kb.referral_kb(link))


@router.message(F.text == Btn.APPS)
async def my_apps(m: Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    await m.answer(texts.my_apps_text(
        await db.video_history(uid), await db.comment_history(uid), await db.exchange_history(uid),
    ))


@router.message(F.text == Btn.INFO)
async def info(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(texts.INFO, reply_markup=kb.info_kb())


@router.callback_query(F.data.startswith("info:"))
async def info_section(cb: CallbackQuery):
    section = cb.data.split(":")[1]
    if section == "rates":
        text = texts.rates_text(await db.list_offers())
    elif section == "rules":
        text = texts.rules_text()
    else:
        start, end, label = month_bounds()
        text = texts.top_text(await db.top_between(start, end), cb.from_user.id, label)
    await cb.message.answer(text)
    await cb.answer()


@fallback_router.message(F.chat.type == "private")
async def unknown(m: Message, state: FSMContext):
    if await state.get_state():
        await m.answer("🤔 Не то, что я жду. Ответь на сообщение выше или нажми «❌ Отмена».")
    else:
        await m.answer("Выбери раздел в меню 👇", reply_markup=kb.main_menu())


@fallback_router.callback_query()
async def stale_button(cb: CallbackQuery):
    await cb.answer("Эта кнопка уже неактуальна. Открой раздел заново из меню.", show_alert=True)
