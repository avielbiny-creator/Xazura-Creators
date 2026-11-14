from urllib.parse import quote

from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import CHANNEL, GROUP_LINK, METHODS, SUPPORT_BOT


class Btn:
    APPLY = "📝 Подать заявку"
    EXCHANGE = "🔄 Обменять комментарии"
    PROFILE = "👤 Профиль"
    APPS = "📋 Мои заявки"
    INFO = "ℹ️ Информация"
    REFERRAL = "👥 Пригласить друга"


MENU_TEXTS = {v for k, v in vars(Btn).items() if not k.startswith("_")}


def main_menu() -> ReplyKeyboardMarkup:
    rows = [
        [Btn.APPLY],
        [Btn.EXCHANGE],
        [Btn.PROFILE, Btn.APPS],
        [Btn.INFO, Btn.REFERRAL],
    ]
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t) for t in row] for row in rows],
        resize_keyboard=True,
        input_field_placeholder="Выбери раздел 👇",
    )


def tg_url(username: str) -> str:
    return f"https://t.me/{username.lstrip('@')}"


def back_kb(callback: str, text: str = "⬅️ Назад") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=text, callback_data=callback)
    return b.as_markup()


def apply_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🎬 За видео", callback_data="apply:video")
    b.button(text="💬 За комментарии", callback_data="apply:comments")
    return b.adjust(2).as_markup()


def info_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="💰 Тарифы", callback_data="info:rates")
    b.button(text="📜 Правила", callback_data="info:rules")
    b.button(text="🏆 Топ месяца", callback_data="info:top")
    if GROUP_LINK:
        b.button(text="📚 Гайды", url=GROUP_LINK)
    b.button(text="🆘 Поддержка", url=tg_url(SUPPORT_BOT))
    return b.adjust(2).as_markup()


def referral_kb(link: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    text = quote(f"Снимай видео и пиши комментарии про {CHANNEL}: получай деньги и Telegram-аккаунты 💸")
    b.button(text="📤 Отправить другу", url=f"https://t.me/share/url?url={quote(link)}&text={text}")
    return b.as_markup()


# ---------- заявки креатора ----------

def _with_cancel(b: InlineKeyboardBuilder, *sizes: int) -> InlineKeyboardMarkup:
    b.button(text="❌ Отмена", callback_data="flow:cancel")
    return b.adjust(*sizes, 1).as_markup() if sizes else b.adjust(1).as_markup()


def cancel_kb() -> InlineKeyboardMarkup:
    return _with_cancel(InlineKeyboardBuilder())


def video_links_kb(has_videos: bool) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if has_videos:
        b.button(text="➡️ Дальше: записи экрана", callback_data="vid:next")
        b.button(text="🗑 Очистить список", callback_data="vid:clear")
    return _with_cancel(b)


def video_methods_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for key, title in METHODS.items():
        b.button(text=title, callback_data=f"vid:m:{key}")
    return _with_cancel(b, 1, 1, 1)


def video_requisites_kb(saved: str | None, username: str | None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if saved:
        b.button(text=f"✅ Как в прошлый раз: {saved[:40]}", callback_data="vid:saved")
    elif username:
        b.button(text=f"⭐ На мой аккаунт @{username}", callback_data="vid:me")
    return _with_cancel(b)


def done_kb(callback: str, text: str = "✅ Готово") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=text, callback_data=callback)
    return _with_cancel(b)


def shots_start_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📝 Примеры комментариев", callback_data="com:ex")
    return _with_cancel(b)


def exchange_kb(offers, balance: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for offer in offers:
        mark = "✅" if balance >= offer["comments"] else "🔒"
        b.button(text=f"{mark} {offer['title']} · {offer['comments']} 💬", callback_data=f"ex:o:{offer['id']}")
    b.button(text="📱 Мои аккаунты", callback_data="ex:accs")
    return b.adjust(1).as_markup()


def account_kb(ex_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔑 Получить код", callback_data=f"ex:code:{ex_id}")
    b.button(text="📱 Мои аккаунты", callback_data="ex:accs")
    b.button(text="🆘 Поддержка", url=tg_url(SUPPORT_BOT))
    return b.adjust(1, 2).as_markup()


def accounts_kb(accounts) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for ex in accounts:
        b.button(text=f"{ex['phone']} · {ex['offer_title'][:30]}", callback_data=f"ex:acc:{ex['id']}")
    b.button(text="⬅️ К обмену", callback_data="ex:no")
    return b.adjust(1).as_markup()


def exchange_confirm_kb(offer_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Обменять", callback_data=f"ex:ok:{offer_id}")
    b.button(text="❌ Отмена", callback_data="ex:no")
    return b.adjust(2).as_markup()


# ---------- админ ----------

def admin_menu_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🎬 Видео на проверке", callback_data="adm:list:v")
    b.button(text="💬 Комменты на проверке", callback_data="adm:list:c")
    b.button(text="💸 К оплате", callback_data="adm:topay")
    b.button(text="🎁 Выдать аккаунты", callback_data="adm:list:e")
    b.button(text="📊 Статистика", callback_data="adm:stats")
    b.button(text="👤 Найти креатора", callback_data="adm:user")
    b.button(text="📢 Рассылка", callback_data="adm:bc")
    b.button(text="🏷 Прайс обмена", callback_data="adm:offers")
    b.button(text="📝 Примеры комментов", callback_data="adm:examples")
    b.button(text="📥 Выгрузка в Excel", callback_data="adm:export")
    b.button(text="⚙️ Настройки", callback_data="adm:settings")
    return b.adjust(2).as_markup()


def admin_export_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="Этот месяц", callback_data="adm:exp:cur")
    b.button(text="Прошлый месяц", callback_data="adm:exp:prev")
    b.button(text="Всё время", callback_data="adm:exp:all")
    b.button(text="⬅️ Меню", callback_data="adm:menu")
    return b.adjust(2, 1, 1).as_markup()


def admin_video_kb(app_id: int, status: str, bonus: bool = False) -> InlineKeyboardMarkup:
    """bonus — выплата без видео (топ, рефералы): просмотров и записей у неё нет."""
    b = InlineKeyboardBuilder()
    if status == "pending":
        b.button(text="✅ Одобрить", callback_data=f"adm:v:ok:{app_id}")
    elif status == "approved":
        b.button(text="💸 Выплачено", callback_data=f"adm:v:pay:{app_id}")
    if status in ("pending", "approved"):
        if not bonus:
            b.button(text="👁 Просмотры", callback_data=f"adm:v:views:{app_id}")
        b.button(text="✏️ Сумма", callback_data=f"adm:v:amt:{app_id}")
        b.button(text="❌ Отклонить", callback_data=f"adm:v:rej:{app_id}")
    if not bonus:
        b.button(text="📎 Записи экрана в личку", callback_data=f"adm:v:proofs:{app_id}")
    if status not in ("pending", "approved"):
        return b.adjust(1).as_markup()
    return b.adjust(1, 2 if bonus else 3, 1).as_markup()


def admin_pay_confirm_kb(app_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Да, деньги отправлены", callback_data=f"adm:v:payok:{app_id}")
    b.button(text="⬅️ Назад", callback_data=f"adm:v:back:{app_id}")
    return b.adjust(1).as_markup()


def admin_comment_kb(app_id: int, status: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if status == "pending":
        b.button(text="✅ Зачислить все", callback_data=f"adm:c:ok:{app_id}")
        b.button(text="✏️ Зачислить часть", callback_data=f"adm:c:part:{app_id}")
        b.button(text="❌ Отклонить", callback_data=f"adm:c:rej:{app_id}")
    b.button(text="📎 Скриншоты в личку", callback_data=f"adm:c:proofs:{app_id}")
    return b.adjust(1, 2, 1).as_markup() if status == "pending" else b.adjust(1).as_markup()


def admin_exchange_kb(ex_id: int, status: str) -> InlineKeyboardMarkup | None:
    if status != "pending":
        return None
    b = InlineKeyboardBuilder()
    b.button(text="✅ Аккаунт выдан", callback_data=f"adm:e:done:{ex_id}")
    b.button(text="↩️ Отменить и вернуть", callback_data=f"adm:e:cancel:{ex_id}")
    return b.adjust(1).as_markup()


def admin_apps_kb(kind: str, items: list[tuple[int, str]]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for app_id, label in items:
        b.button(text=label, callback_data=f"adm:open:{kind}:{app_id}")
    b.button(text="⬅️ Меню", callback_data="adm:menu")
    return b.adjust(1).as_markup()


def admin_user_kb(user) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if user["banned"]:
        b.button(text="✅ Разбанить", callback_data=f"adm:unban:{user['id']}")
    else:
        b.button(text="🚫 Забанить", callback_data=f"adm:ban:{user['id']}")
    b.button(text="✏️ Изменить «выплачено»", callback_data=f"adm:adj:{user['id']}")
    b.button(text="✏️ Изменить баланс комментариев", callback_data=f"adm:bal:{user['id']}")
    b.button(text="⬅️ Меню", callback_data="adm:menu")
    return b.adjust(1).as_markup()


def admin_offers_kb(offers) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="➕ Добавить позицию", callback_data="adm:offer_add")
    for offer in offers:
        b.button(text=f"🗑 {offer['comments']} · {offer['title'][:40]}", callback_data=f"adm:odel:{offer['id']}")
    b.button(text="⬅️ Меню", callback_data="adm:menu")
    return b.adjust(1).as_markup()


def admin_broadcast_kb() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Разослать", callback_data="adm:bc_go")
    b.button(text="❌ Отмена", callback_data="adm:bc_no")
    return b.adjust(2).as_markup()
