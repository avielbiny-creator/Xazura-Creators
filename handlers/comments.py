import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import db
import keyboards as kb
import lolz
import texts
from utils import debounce, notify_admins, tell_admins, user_busy, user_lock

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

MAX_SHOTS = 300
MAX_RECORDS = 10


class Com(StatesGroup):
    shots = State()
    record = State()


async def _drop_kb(cb: CallbackQuery):
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


# ---------- заявка за комментарии ----------

async def begin(m: Message, state: FSMContext, user_id: int):
    pending = await db.pending_comment_of(user_id)
    if pending:
        await m.answer(
            f"⏳ Заявка за комментарии <b>#К{pending['id']}</b> ещё на проверке.\n"
            "Дождись решения, потом можно подать новую."
        )
        return
    await state.set_state(Com.shots)
    await state.update_data(shots=[], dupes=0)
    has_examples = bool(db.get_setting("comment_examples"))
    await m.answer(texts.comments_start(), reply_markup=kb.shots_start_kb(has_examples))


@router.callback_query(F.data == "com:ex")
async def examples(cb: CallbackQuery):
    text = db.get_setting("comment_examples")
    if not text:
        await cb.answer("Примеров пока нет.", show_alert=True)
        return
    await cb.message.answer(texts.examples(text))
    await cb.answer()


@router.message(Com.shots, ~F.text.in_(kb.MENU_TEXTS))
async def got_shot(m: Message, state: FSMContext):
    if m.photo:
        item = {"t": "photo", "id": m.photo[-1].file_id, "uid": m.photo[-1].file_unique_id}
    elif m.document and (m.document.mime_type or "").startswith("image/"):
        item = {"t": "document", "id": m.document.file_id, "uid": m.document.file_unique_id}
    else:
        await m.answer(texts.SHOT_NOT_IMAGE)
        return
    uid = m.from_user.id
    async with user_lock(uid):
        data = await state.get_data()
        shots = data["shots"]
        if len(shots) >= MAX_SHOTS:
            return
        if any(s["uid"] == item["uid"] for s in shots) or await db.shots_taken([item["uid"]]):
            await state.update_data(dupes=data["dupes"] + 1)
        else:
            await state.update_data(shots=shots + [item])

    async def ack():
        if await state.get_state() != Com.shots.state:
            return
        data = await state.get_data()
        count, need = len(data["shots"]), texts.comments_min()
        markup = kb.done_kb("com:next", "➡️ Дальше: запись экрана") if count >= need else kb.cancel_kb()
        await m.answer(texts.shots_progress(count, need, data["dupes"]), reply_markup=markup)

    debounce(uid, ack)


@router.callback_query(Com.shots, F.data == "com:next")
async def shots_done(cb: CallbackQuery, state: FSMContext):
    count, need = len((await state.get_data())["shots"]), texts.comments_min()
    if count < need:
        await cb.answer(f"Нужно минимум {need} скриншотов, сейчас {count}.", show_alert=True)
        return
    await state.set_state(Com.record)
    await state.update_data(records=[])
    await _drop_kb(cb)
    await cb.message.answer(texts.COMMENT_RECORD, reply_markup=kb.cancel_kb())
    await cb.answer()


@router.message(Com.record, ~F.text.in_(kb.MENU_TEXTS))
async def got_record(m: Message, state: FSMContext):
    if m.video:
        item = {"t": "video", "id": m.video.file_id, "rec": True}
    elif m.document and (m.document.mime_type or "").startswith("video/"):
        item = {"t": "document", "id": m.document.file_id, "rec": True}
    else:
        await m.answer(texts.RECORD_NOT_VIDEO)
        return
    uid = m.from_user.id
    async with user_lock(uid):
        records = (await state.get_data())["records"]
        if len(records) >= MAX_RECORDS:
            return
        await state.update_data(records=records + [item])

    async def ack():
        if await state.get_state() != Com.record.state:
            return
        count = len((await state.get_data())["records"])
        await m.answer(texts.records_received(count), reply_markup=kb.done_kb("com:send", "✅ Отправить заявку"))

    debounce(uid, ack)


@router.callback_query(Com.record, F.data == "com:send")
async def send_app(cb: CallbackQuery, state: FSMContext, bot: Bot):
    uid = cb.from_user.id
    async with user_lock(uid):
        if await state.get_state() != Com.record.state:
            return await cb.answer()
        data = await state.get_data()
        if not data["records"]:
            await cb.answer("Сначала отправь запись экрана истории комментариев 🎥", show_alert=True)
            return
        if await db.pending_comment_of(uid):
            await state.clear()
            await _drop_kb(cb)
            await cb.answer("У тебя уже есть заявка за комментарии на проверке.", show_alert=True)
            return
        # Пока креатор собирал заявку, часть скринов могла уйти в другую: убираем их, а не всю заявку
        taken = await db.shots_taken([s["uid"] for s in data["shots"]])
        shots = [s for s in data["shots"] if s["uid"] not in taken]
        if len(shots) < texts.comments_min():
            await state.set_state(Com.shots)
            await state.update_data(shots=shots, dupes=data["dupes"] + len(taken))
            await cb.answer(f"{len(taken)} скриншотов уже были в другой заявке. Дошли ещё и отправь снова.",
                            show_alert=True)
            return
        await state.clear()
        await _drop_kb(cb)
        count = len(shots)
        app_id = await db.create_comment_app(uid, count, shots + data["records"])
    await cb.message.answer(texts.comment_sent(app_id, count), reply_markup=kb.main_menu())
    await cb.answer()
    await notify_admins(bot, "c", app_id)


# ---------- обмен комментариев на аккаунты ----------

async def show_exchange(m: Message, user_id: int, edit: bool = False):
    user = await db.get_user(user_id)
    offers = await db.list_offers()
    text = texts.exchange_text(offers, user["comment_balance"])
    markup = kb.exchange_kb(offers, user["comment_balance"])
    if edit:
        await m.edit_text(text, reply_markup=markup)
    else:
        await m.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("ex:o:"))
async def pick_offer(cb: CallbackQuery):
    offer = await db.get_offer(int(cb.data.split(":")[2]))
    if not offer:
        await cb.answer("Этого аккаунта уже нет в наличии.", show_alert=True)
        return await show_exchange(cb.message, cb.from_user.id, edit=True)
    balance = (await db.get_user(cb.from_user.id))["comment_balance"]
    if balance < offer["comments"]:
        await cb.answer(
            f"Не хватает {offer['comments'] - balance} 💬. Подай заявку за комментарии, чтобы пополнить баланс.",
            show_alert=True,
        )
        return
    await cb.message.edit_text(
        texts.exchange_confirm(offer["title"], offer["comments"], balance),
        reply_markup=kb.exchange_confirm_kb(offer["id"]),
    )
    await cb.answer()


@router.callback_query(F.data == "ex:no")
async def exchange_back(cb: CallbackQuery):
    await show_exchange(cb.message, cb.from_user.id, edit=True)
    await cb.answer()


@router.callback_query(F.data.startswith("ex:ok:"))
async def exchange_ok(cb: CallbackQuery, bot: Bot):
    uid = cb.from_user.id
    offer = await db.get_offer(int(cb.data.split(":")[2]))
    if not offer:
        await cb.answer("Этого аккаунта уже нет в наличии.", show_alert=True)
        return await show_exchange(cb.message, uid, edit=True)
    if user_busy(uid):
        return await cb.answer("Уже покупаю аккаунт, подожди пару секунд ⏳")
    auto = bool(offer["code"] and lolz.client)
    async with user_lock(uid):
        ex_id = await db.create_exchange(uid, offer["title"], offer["comments"], "buying" if auto else "pending")
        if ex_id is None:
            await cb.answer("Не хватает комментариев на балансе.", show_alert=True)
            return await show_exchange(cb.message, uid, edit=True)
        if not auto:
            # Страна без кода LOLZ: аккаунт выдаёт админ вручную
            await cb.message.edit_text(texts.exchange_sent(ex_id, offer["title"]))
            await cb.answer()
            return await notify_admins(bot, "e", ex_id)

        await cb.answer()
        await cb.message.edit_text(texts.exchange_buying(offer["title"]))
        try:
            res = await lolz.buy_account(offer["code"], offer["max_price"], int(db.num("lolz_min_rating")))
        except Exception:
            # Непонятный ответ LOLZ: аккаунт мог купиться, поэтому не возвращаем комментарии, а зовём админа
            logging.exception("Обмен #О%s: сбой автопокупки", ex_id)
            await db.exchange_to_admin(ex_id)
            await cb.message.edit_text(texts.exchange_sent(ex_id, offer["title"]))
            return await notify_admins(bot, "e", ex_id)
        if res.item and not lolz.phone_of(res.item):
            # Аккаунт куплен, но LOLZ не отдал номер: без номера креатор не войдёт, выдаёт админ
            await db.exchange_to_admin(ex_id, res.item["item_id"], res.cost)
            await cb.message.edit_text(texts.exchange_sent(ex_id, offer["title"]))
            await tell_admins(bot, texts.no_phone_admin(ex_id, res.item["item_id"]))
            return await notify_admins(bot, "e", ex_id)
        if res.item:
            await db.complete_exchange(ex_id, res.item["item_id"], res.phone, res.cost)
        else:
            await db.finish_exchange(ex_id, done=False, admin_name="автовыдача", from_status="buying")
    ex = await db.get_exchange(ex_id)
    user = await db.get_user(uid)
    if res.item:
        await cb.message.edit_text(texts.account_view(ex, fresh=True), reply_markup=kb.account_kb(ex_id))
        await tell_admins(bot, texts.auto_issued_admin(ex, user))
    else:
        await cb.message.edit_text(texts.exchange_failed(ex, user["comment_balance"]), reply_markup=kb.back_kb("ex:no"))
        await tell_admins(bot, texts.auto_failed_admin(ex, user, res.no_money, res.error))


@router.callback_query(F.data == "ex:accs")
async def my_accounts(cb: CallbackQuery):
    accounts = await db.user_accounts(cb.from_user.id)
    await cb.message.edit_text(texts.accounts_list(accounts), reply_markup=kb.accounts_kb(accounts))
    await cb.answer()


async def _own_account(cb: CallbackQuery):
    ex = await db.get_exchange(int(cb.data.split(":")[2]))
    if not ex or ex["user_id"] != cb.from_user.id or ex["status"] != "done" or not ex["phone"]:
        await cb.answer("Аккаунт не найден", show_alert=True)
        return None
    return ex


@router.callback_query(F.data.startswith("ex:acc:"))
async def account(cb: CallbackQuery):
    if ex := await _own_account(cb):
        await cb.message.edit_text(texts.account_view(ex), reply_markup=kb.account_kb(ex["id"]))
        await cb.answer()


@router.callback_query(F.data.startswith("ex:code:"))
async def login_code(cb: CallbackQuery):
    if not (ex := await _own_account(cb)):
        return
    code, error = await lolz.fetch_code(cb.from_user.id, ex["item_id"])
    if error:
        return await cb.answer(error, show_alert=True)
    await cb.answer()
    await cb.message.answer(texts.login_code(ex["phone"], code))
