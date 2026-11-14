from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import db
import keyboards as kb
import texts
from config import METHODS
from utils import debounce, esc, level_info, notify_admins, parse_profile, parse_video_links, user_lock

router = Router()
router.message.filter(F.chat.type == "private")
router.callback_query.filter(F.message.chat.type == "private")

MAX_VIDEOS = 30
MAX_PROOFS = 20


class Vid(StatesGroup):
    profile = State()
    links = State()
    proofs = State()
    method = State()
    requisites = State()
    confirm = State()


async def _drop_kb(cb: CallbackQuery):
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass


# ---------- шаг 1: аккаунт ----------

async def begin(m: Message, state: FSMContext, user_id: int):
    pending = await db.pending_video_of(user_id)
    if pending:
        await m.answer(
            f"⏳ У тебя уже есть заявка за видео <b>#{pending['id']}</b> на проверке.\n"
            "Дождись решения, потом можно подать новую."
        )
        return
    await state.set_state(Vid.profile)
    await m.answer(texts.VIDEO_PROFILE, reply_markup=kb.cancel_kb())


@router.message(Vid.profile, F.text & ~F.text.in_(kb.MENU_TEXTS))
async def got_profile(m: Message, state: FSMContext):
    profile = parse_profile(m.text)
    if not profile:
        await m.answer(texts.VIDEO_PROFILE_BAD, reply_markup=kb.cancel_kb())
        return
    await state.update_data(profile=profile, videos=[])
    await state.set_state(Vid.links)
    await m.answer(texts.video_links(profile), reply_markup=kb.cancel_kb())


# ---------- шаг 2: видео ----------

@router.message(Vid.links, F.text & ~F.text.in_(kb.MENU_TEXTS))
async def got_links(m: Message, state: FSMContext):
    async with user_lock(m.from_user.id):
        parsed, problems = await parse_video_links(m.text)
        data = await state.get_data()
        videos, profile = list(data["videos"]), data["profile"]
        for video in parsed:
            url = esc(video["url"])
            if len(videos) >= MAX_VIDEOS:
                problems.append(f"в одной заявке максимум {MAX_VIDEOS} видео")
                break
            if any(v["key"] == video["key"] for v in videos):
                problems.append(f"{url}: уже в списке")
            elif video["author"] and video["author"] != profile:
                problems.append(f"{url}: видео с аккаунта @{esc(video['author'])}, а заявка от @{esc(profile)}")
            elif await db.video_taken(video["key"]):
                problems.append(f"{url}: уже было в другой заявке")
            else:
                videos.append(video)
        await state.update_data(videos=videos)
    await m.answer(texts.videos_list(videos, problems), reply_markup=kb.video_links_kb(bool(videos)))


@router.callback_query(Vid.links, F.data == "vid:clear")
async def clear_links(cb: CallbackQuery, state: FSMContext):
    await state.update_data(videos=[])
    await cb.message.edit_text("🗑 Список очищен. Отправь ссылки на видео заново.", reply_markup=kb.cancel_kb())
    await cb.answer()


@router.callback_query(Vid.links, F.data == "vid:next")
async def links_done(cb: CallbackQuery, state: FSMContext):
    if not (await state.get_data())["videos"]:
        return await cb.answer("Сначала отправь ссылки на видео", show_alert=True)
    await state.set_state(Vid.proofs)
    await state.update_data(proofs=[])
    await _drop_kb(cb)
    await cb.message.answer(texts.VIDEO_PROOF, reply_markup=kb.done_kb("vid:proofs", "➡️ Далее"))
    await cb.answer()


# ---------- шаг 3: записи экрана ----------

@router.message(Vid.proofs, ~F.text.in_(kb.MENU_TEXTS))
async def got_proof(m: Message, state: FSMContext):
    if m.video:
        item = {"t": "video", "id": m.video.file_id}
    elif m.document and (m.document.mime_type or "").startswith("video/"):
        item = {"t": "document", "id": m.document.file_id}
    else:
        await m.answer(texts.PROOF_NOT_VIDEO, reply_markup=kb.done_kb("vid:proofs", "➡️ Далее"))
        return
    uid = m.from_user.id
    async with user_lock(uid):
        proofs = (await state.get_data())["proofs"]
        if len(proofs) >= MAX_PROOFS:
            return
        await state.update_data(proofs=proofs + [item])

    async def ack():
        if await state.get_state() != Vid.proofs.state:
            return
        count = len((await state.get_data())["proofs"])
        await m.answer(texts.proofs_received(count), reply_markup=kb.done_kb("vid:proofs", "➡️ Далее"))

    debounce(uid, ack)


@router.callback_query(Vid.proofs, F.data == "vid:proofs")
async def proofs_done(cb: CallbackQuery, state: FSMContext):
    if not (await state.get_data())["proofs"]:
        return await cb.answer("Сначала отправь запись экрана 🎥", show_alert=True)
    await _drop_kb(cb)
    await state.set_state(Vid.method)
    await cb.message.answer(texts.video_method(), reply_markup=kb.video_methods_kb())
    await cb.answer()


# ---------- шаг 4: оплата ----------

@router.callback_query(Vid.method, F.data.startswith("vid:m:"))
async def pick_method(cb: CallbackQuery, state: FSMContext):
    method = cb.data.split(":")[2]
    if method not in METHODS:
        return await cb.answer()
    user = await db.get_user(cb.from_user.id)
    saved = user["requisites"] if user["method"] == method else None
    await state.update_data(method=method)
    await state.set_state(Vid.requisites)
    markup = kb.video_requisites_kb(saved, cb.from_user.username if method == "stars" else None)
    await cb.message.edit_text(texts.REQUISITES_PROMPT[method], reply_markup=markup)
    await cb.answer()


async def _confirm(m: Message, state: FSMContext, user_id: int, requisites: str):
    data = await state.get_data()
    await db.set_requisites(user_id, data["method"], requisites)
    await state.update_data(requisites=requisites)
    await state.set_state(Vid.confirm)
    await m.answer(
        texts.video_confirm(data["profile"], data["videos"], len(data["proofs"]), data["method"], requisites),
        reply_markup=kb.done_kb("vid:send", "✅ Отправить заявку"),
    )


@router.message(Vid.requisites, F.text & ~F.text.in_(kb.MENU_TEXTS))
async def got_requisites(m: Message, state: FSMContext):
    if len(m.text) > 200:
        await m.answer("⚠️ Слишком длинно. Отправь только реквизиты.", reply_markup=kb.cancel_kb())
        return
    await _confirm(m, state, m.from_user.id, m.text.strip())


@router.callback_query(Vid.requisites, F.data.in_({"vid:saved", "vid:me"}))
async def requisites_button(cb: CallbackQuery, state: FSMContext):
    if cb.data == "vid:me":
        requisites = f"@{cb.from_user.username}" if cb.from_user.username else None
    else:
        requisites = (await db.get_user(cb.from_user.id))["requisites"]
    if not requisites:
        return await cb.answer("Не нашёл реквизиты, отправь их сообщением.", show_alert=True)
    await _drop_kb(cb)
    await _confirm(cb.message, state, cb.from_user.id, requisites)
    await cb.answer()


@router.callback_query(Vid.confirm, F.data == "vid:send")
async def send_app(cb: CallbackQuery, state: FSMContext, bot: Bot):
    uid = cb.from_user.id
    async with user_lock(uid):
        if await state.get_state() != Vid.confirm.state:
            return await cb.answer()
        data = await state.get_data()
        await state.clear()
        await _drop_kb(cb)
        if await db.pending_video_of(uid):
            await cb.answer("У тебя уже есть заявка за видео на проверке.", show_alert=True)
            return
        for video in data["videos"]:
            if await db.video_taken(video["key"]):
                await cb.answer("Одно из видео уже есть в другой заявке. Подай заявку заново.", show_alert=True)
                return
        bonus = level_info((await db.get_user(uid))["total_paid"])[1]
        app_id = await db.create_video_app(
            uid, data["profile"], data["videos"], db.num("rate"), bonus, data["method"], data["requisites"],
            data["proofs"],
        )
    await cb.message.answer(texts.video_sent(app_id), reply_markup=kb.main_menu())
    await cb.answer()
    await notify_admins(bot, "v", app_id)
