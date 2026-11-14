import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}
CHANNEL = os.getenv("CHANNEL", "@XazuraShop").strip()
SUPPORT_BOT = os.getenv("SUPPORT_BOT", "@XazuraSup_Bot").strip()
# Группа с гайдами, идеями и материалами
GROUP_LINK = os.getenv("GROUP_LINK", "").strip()
# Без токена аккаунты за комментарии выдаёт админ вручную
LOLZ_TOKEN = os.getenv("LOLZ_TOKEN", "").strip()
LOLZ_API_URL = os.getenv("LOLZ_API_URL", "https://api.lzt.market").strip()

# На сервере базу кладём в отдельную папку (volume в Docker)
DATA_DIR = Path(os.getenv("DATA_DIR", BASE_DIR))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "data.db"

METHODS = {"rub": "💳 Рубли (карта / СБП)", "crypto": "🪙 Крипта (USDT)", "stars": "⭐ Telegram Stars"}
VIDEO_STATUSES = {
    "pending": "⏳ На проверке",
    "approved": "👍 Одобрена, ждёт выплаты",
    "paid": "✅ Выплачено",
    "rejected": "❌ Отклонена",
}
COMMENT_STATUSES = {"pending": "⏳ На проверке", "approved": "✅ Зачислено", "rejected": "❌ Отклонена"}
EXCHANGE_STATUSES = {
    "buying": "⏳ Покупается",
    "pending": "⏳ Ждёт выдачи",
    "done": "✅ Выдан",
    "cancelled": "↩️ Отменён, комментарии возвращены",
}

# (сколько всего выплачено ₽, название, бонус к ставке %)
LEVELS = [
    (0, "🥉 Новичок", 0),
    (1000, "🥈 Креатор", 5),
    (5000, "🥇 Профи", 10),
    (15000, "💎 Легенда", 15),
]

# Значения по умолчанию. Меняются из бота командой /set ключ значение
DEFAULT_SETTINGS = {
    "rate": 150,
    "min_video": 1000,
    "min_total": 2000,
    "comments_min": 40,
    "rub_per_usd": 75,
    "rub_per_star": 1.15,
    "top_rewards": "1000,500,250",
    "ref_bonus": 10,
    "ref_comments_pct": 10,
    "ref_video_pct": 5,
    "ref_min_payout": 100,
    "nudge_days": 7,
    "lolz_min_rating": 50,
}

SETTING_NAMES = {
    "rate": "Ставка, ₽ за 1 000 просмотров",
    "min_video": "Учёт видео от (просм.)",
    "min_total": "Выплата от (просм. суммарно)",
    "comments_min": "Заявка за комментарии от (шт.)",
    "rub_per_usd": "Курс: ₽ за 1$",
    "rub_per_star": "Курс: ₽ за 1⭐",
    "top_rewards": "Бонусы топа месяца, ₽ за 1, 2, 3 место",
    "ref_bonus": "Рефералы: разовый бонус за первую выплату друга, 💬",
    "ref_comments_pct": "Рефералы: % от комментариев друга",
    "ref_video_pct": "Рефералы: % от выплат друга за видео",
    "ref_min_payout": "Рефералы: выплачивать рубли от, ₽",
    "nudge_days": "Напомнить креатору через N дней без заявок (0 = выкл.)",
    "lolz_min_rating": "Мин. рейтинг продавца LOLZ (👍 − 👎)",
}
# Не числа: проверяются отдельно
LIST_SETTINGS = {"top_rewards"}
# Можно ставить 0
ZERO_OK_SETTINGS = {"ref_bonus", "ref_comments_pct", "ref_video_pct", "nudge_days", "lolz_min_rating"}

# Через сколько часов напоминать админам о непроверенных заявках
ADMIN_REMIND_HOURS = 24

# Прайс обмена при первом запуске. Дальше меняется в админке.
# (комментарии, название, код страны на LOLZ, потолок закупки ₽ — как в магазине)
DEFAULT_OFFERS = [
    (45, "🇲🇲 Мьянма [+95]", "MM", 29),
    (60, "🇨🇴 Колумбия [+57]", "CO", 27),
    (75, "🇺🇸 США [+1]", "US", 35),
    (100, "🇨🇳 Китай [+86]", "CN", 50),
    (110, "🇧🇩 Бангладеш [+880]", "BD", 55),
    (120, "🇺🇿 Узбекистан [+998]", "UZ", 50),
    (199, "🇯🇵 Япония [+81] (✅лучшее качество)", "JP", 95),
]
