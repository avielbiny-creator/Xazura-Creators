from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message

import db
from config import SUPPORT_BOT
from utils import is_admin


def _is_bind(event) -> bool:
    return isinstance(event, Message) and (event.text or "").split("@")[0] == "/bind"


class AccessMiddleware(BaseMiddleware):
    """Личка: сохраняет креатора в базу и не пускает забаненных.
    Группы: только админы и только в админ-группе (или команда /bind, чтобы её привязать)."""

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        chat = data.get("event_chat")
        if user is None or user.is_bot:
            return None

        if chat is not None and chat.type != "private":
            in_admin_chat = str(chat.id) == db.get_setting("admin_chat")
            if is_admin(user.id) and (in_admin_chat or _is_bind(event)):
                return await handler(event, data)
            return None

        await db.upsert_user(user.id, user.username, user.full_name)
        if is_admin(user.id):
            return await handler(event, data)

        row = await db.get_user(user.id)
        if row["banned"]:
            text = f"🚫 Доступ к боту ограничен. Вопросы: {SUPPORT_BOT}"
            if isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            else:
                await event.answer(text)
            return None

        return await handler(event, data)
