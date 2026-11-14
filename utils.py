import asyncio
import html
import json
import logging
import math
import re
from contextlib import asynccontextmanager
from datetime import datetime
from urllib.parse import urlsplit

import aiohttp
from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramNetworkError, TelegramRetryAfter
from aiogram.types import BufferedInputFile, InputMediaDocument, InputMediaPhoto, InputMediaVideo, Message, User

import db
import keyboards as kb
from config import ADMIN_IDS, BASE_DIR, COMMENT_STATUSES, EXCHANGE_STATUSES, LEVELS, METHODS, VIDEO_STATUSES

esc = html.escape


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def admin_name(user: User) -> str:
    return f"@{user.username}" if user.username else user.full_name


def fmt(n) -> str:
    n = float(n)
    if n.is_integer():
        return f"{int(n):,}".replace(",", " ")
    return f"{n:,.2f}".replace(",", " ")


MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август",
          "сентябрь", "октябрь", "ноябрь", "декабрь"]


def month_start() -> int:
    return month_bounds()[0]


def month_bounds(shift: int = 0) -> tuple[int, int, str]:
    """(начало, конец, «ноябрь 2026») месяца: 0 текущий, -1 прошлый."""
    first = datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    index = first.year * 12 + first.month - 1 + shift
    start = first.replace(year=index // 12, month=index % 12 + 1)
    end = first.replace(year=(index + 1) // 12, month=(index + 1) % 12 + 1)
    return int(start.timestamp()), int(end.timestamp()), f"{MONTHS[start.month - 1]} {start.year}"


def top_rewards() -> list[int]:
    return [int(x) for x in db.text("top_rewards").split(",") if x.strip().isdigit()]


def date(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y")


# ---------- деньги и уровни ----------

def level_info(total_paid: int):
    """Возвращает (название, бонус %, следующий уровень или None)."""
    idx = 0
    for i, (threshold, _, _) in enumerate(LEVELS):
        if total_paid >= threshold:
            idx = i
    _, name, bonus = LEVELS[idx]
    nxt = LEVELS[idx + 1] if idx + 1 < len(LEVELS) else None
    return name, bonus, nxt


def min_video() -> int:
    return int(db.num("min_video"))


def min_total() -> int:
    return int(db.num("min_total"))


def calc_amount(views: int, rate: float, bonus_pct: int) -> int:
    return round(views / 1000 * rate * (1 + bonus_pct / 100))


def money(rub: int, method: str | None = None) -> str:
    text = f"{fmt(rub)} ₽"
    if method == "crypto":
        text += f" ≈ {fmt(round(rub / db.num('rub_per_usd'), 2))} $"
    elif method == "stars":
        text += f" ≈ {fmt(math.ceil(rub / db.num('rub_per_star')))} ⭐"
    return text


# ---------- ввод ----------

def parse_views(text: str) -> int | None:
    t = text.lower().replace(" ", "").replace(" ", "").rstrip(".")
    mult = 1
    for suffix, m in (("тыс", 1000), ("к", 1000), ("k", 1000), ("млн", 1_000_000), ("м", 1_000_000), ("m", 1_000_000)):
        if t.endswith(suffix):
            t, mult = t[: -len(suffix)], m
            break
    t = t.replace(",", ".")
    if mult == 1:
        t = t.replace(".", "")
    try:
        value = int(float(t) * mult)
    except (ValueError, OverflowError):  # «inf», «nan», мусор
        return None
    return value if 0 < value < 10**10 else None


_TT_LINK = re.compile(r"(?:https?://)?(?:[\w-]+\.)*tiktok\.com/\S*", re.I)
_TT_VIDEO = re.compile(r"/@([\w.\-]+)/(?:video|photo)/(\d+)", re.I)
_TT_VIDEO_BARE = re.compile(r"/(?:v|video)/(\d+)", re.I)
_TT_PROFILE = re.compile(r"tiktok\.com/@([\w.\-]+)/?(?:\?.*)?$", re.I)


async def _resolve(url: str) -> str:
    """Короткие ссылки vm./vt.tiktok.com раскрываем в полную, чтобы узнать ID видео (ловим повторы)."""
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(url, allow_redirects=True) as resp:
                return str(resp.url)
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logging.info("Не удалось раскрыть ссылку %s: %s", url, e)
        return url


async def parse_video_link(raw: str) -> dict | None:
    """{'url', 'key'} или None, если это не ссылка на видео TikTok."""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    host = parts.netloc.lower()
    if not host.endswith("tiktok.com"):
        return None
    url = raw
    if host.split(".")[0] in ("vm", "vt") or parts.path.startswith("/t/"):
        url = await _resolve(raw)
    path = urlsplit(url).path
    if m := _TT_VIDEO.search(path):
        author, vid = m.group(1).lower(), m.group(2)
        return {"url": f"https://www.tiktok.com/@{author}/video/{vid}", "key": f"tt:{vid}", "author": author}
    if m := _TT_VIDEO_BARE.search(path):
        return {"url": f"https://www.tiktok.com/video/{m.group(1)}", "key": f"tt:{m.group(1)}", "author": None}
    short = urlsplit(raw)
    if short.netloc.lower().split(".")[0] in ("vm", "vt") and short.path.strip("/"):
        # Не раскрылась: принимаем как есть, админ проверит руками
        clean = f"https://{short.netloc.lower()}{short.path.rstrip('/')}"
        return {"url": clean, "key": f"url:{clean}", "author": None}
    return None


def parse_profile(text: str) -> str | None:
    """Ник из ссылки на TikTok-профиль (tiktok.com/@ник) или просто @ника."""
    text = text.strip()
    if m := _TT_PROFILE.search(text):
        return m.group(1).lower().rstrip(".")
    if m := re.fullmatch(r"@([\w.]{2,24})", text):
        return m.group(1).lower().rstrip(".")
    return None


async def parse_video_links(text: str) -> tuple[list[dict], list[str]]:
    """Все ссылки на видео TikTok из сообщения. Возвращает (видео, ошибки)."""
    videos, errors = [], []
    links = [m.group(0).rstrip(".,;)") for m in _TT_LINK.finditer(text)]
    if not links:
        errors.append("в сообщении нет ссылок на TikTok")
    for link in links:
        video = await parse_video_link(link)
        if video:
            videos.append(video)
        else:
            errors.append(f"{esc(link[:60])}: это не ссылка на видео")
    return videos, errors


# ---------- блокировка на креатора ----------

_user_locks: dict[int, list] = {}


@asynccontextmanager
async def user_lock(user_id: int):
    """Действия одного креатора выполняются по очереди (альбом из 10 файлов, двойное нажатие кнопки).
    Замок удаляется, когда больше никому не нужен, чтобы словарь не рос с каждым новым креатором."""
    entry = _user_locks.setdefault(user_id, [asyncio.Lock(), 0])
    entry[1] += 1
    try:
        async with entry[0]:
            yield
    finally:
        entry[1] -= 1
        if entry[1] == 0:
            del _user_locks[user_id]


def user_busy(user_id: int) -> bool:
    return user_id in _user_locks


# ---------- ответ на пачку файлов ----------

_debounce_tasks: dict[int, asyncio.Task] = {}


def debounce(key: int, callback, delay: float = 1.5):
    """Альбом приходит пачкой сообщений: отвечаем один раз, когда пачка закончилась."""
    old = _debounce_tasks.get(key)
    if old and not old.done():
        old.cancel()

    async def run():
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if _debounce_tasks.get(key) is task:
            del _debounce_tasks[key]
        try:
            await callback()
        except TelegramAPIError as e:
            logging.warning("Не удалось ответить на пачку файлов: %s", e)

    task = asyncio.create_task(run())
    _debounce_tasks[key] = task


# ---------- приветственная картинка ----------

START_BANNER = BASE_DIR / "banners" / "start.jpg"
_banner_id: str | None = None


async def send_start_banner(message: Message, text: str, reply_markup=None):
    """Картинка с текстом в подписи. Если Telegram не принял её за 10 секунд, шлём просто текст."""
    global _banner_id
    if not START_BANNER.exists():
        return await message.answer(text, reply_markup=reply_markup)
    photo = _banner_id or BufferedInputFile(START_BANNER.read_bytes(), START_BANNER.name)
    try:
        sent = await message.bot.send_photo(
            message.chat.id, photo, caption=text, reply_markup=reply_markup, request_timeout=10
        )
    except (TelegramBadRequest, TelegramNetworkError) as e:
        logging.warning("Не удалось отправить картинку: %s", e)
        _banner_id = None
        return await message.answer(text, reply_markup=reply_markup)
    _banner_id = sent.photo[-1].file_id
    return sent


# ---------- отправка медиа ----------

_INPUT = {"photo": InputMediaPhoto, "video": InputMediaVideo, "document": InputMediaDocument}


async def _retry(call):
    """Повторяет запрос, если Telegram просит подождать (большие пачки скринов)."""
    for _ in range(5):
        try:
            return await call()
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 1)
    return await call()


async def _send_one(bot: Bot, chat_id: int, kind: str, media):
    if kind == "photo":
        return await _retry(lambda: bot.send_photo(chat_id=chat_id, photo=media))
    if kind == "video":
        return await _retry(lambda: bot.send_video(chat_id=chat_id, video=media))
    return await _retry(lambda: bot.send_document(chat_id=chat_id, document=media))


def _chunks(items: list[tuple]):
    """Документы нельзя смешивать с фото/видео в одном альбоме, и в альбоме максимум 10 штук."""
    visual = [i for i in items if i[0] != "document"]
    docs = [i for i in items if i[0] == "document"]
    for group in (visual, docs):
        for start in range(0, len(group), 10):
            yield group[start:start + 10]


async def send_proofs(bot: Bot, chat_id: int, proofs: list[dict]):
    for chunk in _chunks([(p["t"], p["id"]) for p in proofs]):
        try:
            if len(chunk) == 1:
                await _send_one(bot, chat_id, *chunk[0])
            else:
                media = [_INPUT[k](media=m) for k, m in chunk]
                await _retry(lambda: bot.send_media_group(chat_id=chat_id, media=media))
        except TelegramAPIError as e:
            logging.warning("Не удалось отправить пруфы альбомом: %s", e)
            for kind, media in chunk:
                try:
                    await _send_one(bot, chat_id, kind, media)
                except TelegramAPIError as e2:
                    logging.error("Не удалось отправить пруф: %s", e2)
        await asyncio.sleep(1)


# ---------- карточки заявок для админа ----------

def user_line(user) -> str:
    name = esc(user["full_name"] or "Без имени")
    if user["username"]:
        name += f" (@{esc(user['username'])})"
    return name


async def video_card(app_id: int) -> str:
    app = await db.get_video_app(app_id)
    user = await db.get_user(app["user_id"])
    videos = await db.get_app_videos(app_id)
    level, _, _ = level_info(user["total_paid"])
    lines = [
        f"🎬 <b>Видео · заявка #{app['id']}</b> · {VIDEO_STATUSES[app['status']]}",
        "",
        f"👤 {user_line(user)}",
        f"🆔 <code>{user['id']}</code> · {level} · выплачено всего {fmt(user['total_paid'])} ₽",
        "",
    ]
    if app["note"]:
        lines.append(f"<b>{esc(app['note'])}</b>")
    else:
        bonus = f" +{app['bonus_pct']}% за уровень" if app["bonus_pct"] else ""
        views = f"<b>{fmt(app['total_views'])}</b>" if app["total_views"] else "⚠️ не указаны, нажми «👁 Просмотры»"
        lines += [f"🎵 Аккаунт: @{esc(app['profile'] or '?')}", "", "<b>Видео:</b>"]
        lines += [f"{i}. {esc(v['url'])}" for i, v in enumerate(videos, 1)]
        lines += [
            "",
            f"📹 Записей экрана: {len(json.loads(app['proofs']))}",
            f"👁 Просмотры: {views}",
            f"📈 Ставка: {fmt(app['rate'])} ₽/1к{bonus}",
        ]
    amount = money(app["amount_rub"], app["method"]) if app["amount_rub"] else "посчитается после ввода просмотров"
    lines += [
        "",
        f"💰 К выплате: <b>{amount}</b>",
        f"{METHODS[app['method']]}: <code>{esc(app['requisites'])}</code>",
    ]
    if app["status"] == "rejected" and app["reason"]:
        lines.append(f"\n📝 Причина отказа: {esc(app['reason'])}")
    if app["admin_name"]:
        lines.append(f"\n👮 {esc(app['admin_name'])}")
    return "\n".join(lines)


async def comment_card(app_id: int) -> str:
    app = await db.get_comment_app(app_id)
    user = await db.get_user(app["user_id"])
    proofs = json.loads(app["proofs"])
    records = sum(1 for p in proofs if p.get("rec"))
    lines = [
        f"💬 <b>Комментарии · заявка #К{app['id']}</b> · {COMMENT_STATUSES[app['status']]}",
        "",
        f"👤 {user_line(user)}",
        f"🆔 <code>{user['id']}</code> · баланс: {fmt(user['comment_balance'])} 💬",
        "",
        f"📸 Скриншотов: <b>{app['count']}</b>",
        f"📹 Записей истории комментариев: <b>{records}</b>",
    ]
    if app["status"] == "approved":
        lines.append(f"💬 Зачислено: <b>{app['credited']}</b>")
    if app["status"] == "rejected" and app["reason"]:
        lines.append(f"\n📝 Причина отказа: {esc(app['reason'])}")
    if app["admin_name"]:
        lines.append(f"\n👮 {esc(app['admin_name'])}")
    return "\n".join(lines)


async def exchange_card(ex_id: int) -> str:
    ex = await db.get_exchange(ex_id)
    user = await db.get_user(ex["user_id"])
    lines = [
        f"🎁 <b>Обмен #О{ex['id']}</b> · {EXCHANGE_STATUSES[ex['status']]}",
        "",
        f"👤 {user_line(user)}",
        f"🆔 <code>{user['id']}</code> · остаток на балансе: {fmt(user['comment_balance'])} 💬",
        "",
        f"Выдать: <b>{esc(ex['offer_title'])}</b>",
        f"Списано: {fmt(ex['cost'])} 💬",
    ]
    if ex["phone"]:
        lines.append(f"📱 <code>{esc(ex['phone'])}</code> · закупка {fmt(round(ex['buy_price'] or 0, 2))} ₽")
    if ex["status"] == "pending":
        lines.append("\nОтправь аккаунт креатору и нажми «✅ Аккаунт выдан».")
    if ex["admin_name"]:
        lines.append(f"\n👮 {esc(ex['admin_name'])}")
    return "\n".join(lines)


async def card_of(kind: str, app_id: int) -> tuple[str, object]:
    """Текст и кнопки карточки по её текущему статусу. kind: v (видео), c (комментарии), e (обмен)."""
    if kind == "v":
        app = await db.get_video_app(app_id)
        return await video_card(app_id), kb.admin_video_kb(app_id, app["status"], bool(app["note"]))
    if kind == "e":
        ex = await db.get_exchange(app_id)
        return await exchange_card(app_id), kb.admin_exchange_kb(app_id, ex["status"])
    app = await db.get_comment_app(app_id)
    return await comment_card(app_id), kb.admin_comment_kb(app_id, app["status"])


def admin_targets() -> list[int]:
    """Куда слать новые заявки: в привязанную админ-группу, иначе в личку каждому админу."""
    chat = db.get_setting("admin_chat")
    return [int(chat)] if chat else list(ADMIN_IDS)


async def tell_admins(bot: Bot, text: str):
    """Просто сообщение админам, без карточки и кнопок."""
    for chat_id in admin_targets():
        try:
            await bot.send_message(chat_id, text)
        except TelegramAPIError as e:
            logging.error("Не удалось написать в чат %s: %s", chat_id, e)


async def notify_admins(bot: Bot, kind: str, app_id: int):
    text, markup = await card_of(kind, app_id)
    text = ("🔔 <b>Новый обмен</b>\n\n" if kind == "e" else "🔔 <b>Новая заявка</b>\n\n") + text
    delivered = False
    for chat_id in admin_targets():
        try:
            await bot.send_message(chat_id, text, reply_markup=markup)
            delivered = True
        except TelegramAPIError as e:
            logging.error("Не удалось отправить заявку в чат %s: %s", chat_id, e)
    if not delivered and db.get_setting("admin_chat"):
        # Бота убрали из админ-группы: чтобы заявка не потерялась, шлём админам в личку
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, text, reply_markup=markup)
            except TelegramAPIError as e:
                logging.error("Не удалось отправить заявку админу %s: %s", admin_id, e)
