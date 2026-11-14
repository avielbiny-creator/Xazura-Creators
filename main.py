import asyncio
import logging
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BotCommand, BotCommandScopeAllPrivateChats

import db
import lolz
import scheduler
import texts
from config import ADMIN_IDS, BOT_TOKEN, DATA_DIR, LOLZ_API_URL, LOLZ_TOKEN
from handlers import admin, comments, user, video
from middlewares import AccessMiddleware
from storage import DBStorage
from utils import tell_admins


async def set_commands(bot: Bot):
    """Меню команд. Не ждём его при старте: если Telegram тупит с этим методом, бот всё равно работает."""
    try:
        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Главное меню"),
                BotCommand(command="id", description="Мой Telegram ID"),
                BotCommand(command="cancel", description="Отменить действие"),
            ],
            scope=BotCommandScopeAllPrivateChats(),
            request_timeout=20,
        )
    except TelegramAPIError as e:
        logging.warning("Не удалось обновить меню команд: %s", e)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            # Не больше 3 файлов по 5 МБ, иначе лог за месяцы работы забьёт диск
            RotatingFileHandler(DATA_DIR / "bot.log", maxBytes=5_000_000, backupCount=2, encoding="utf-8"),
        ],
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not BOT_TOKEN:
        raise SystemExit("Укажи BOT_TOKEN в файле .env")
    if not ADMIN_IDS:
        logging.warning("ADMIN_IDS в .env пустой: заявки никому не придут. Узнай свой ID командой /id")

    stuck = await db.init()
    if LOLZ_TOKEN:
        lolz.client = lolz.LolzClient(LOLZ_TOKEN, LOLZ_API_URL)
    else:
        logging.warning("LOLZ_TOKEN не задан: аккаунты за комментарии выдаются вручную")
    if not db.get_setting("admin_chat"):
        logging.warning("Админ-группа не привязана: заявки идут админам в личку. Напиши /bind в группе")
    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    dp = Dispatcher(storage=DBStorage())

    access = AccessMiddleware()
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    # Порядок важен: админка → меню → заявки → всё остальное
    dp.include_routers(admin.router, user.router, video.router, comments.router, user.fallback_router)

    me = await bot.get_me()
    logging.info("Бот @%s запущен", me.username)
    if stuck:
        logging.warning("Обменов, прерванных посреди покупки: %s", stuck)
        await tell_admins(bot, texts.stuck_exchanges(stuck))
    commands = asyncio.create_task(set_commands(bot))
    jobs = asyncio.create_task(scheduler.run(bot))
    try:
        await dp.start_polling(bot)
    finally:
        commands.cancel()
        jobs.cancel()
        if lolz.client:
            await lolz.client.close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
