import re

import db
from config import CHANNEL, COMMENT_STATUSES, EXCHANGE_STATUSES, LEVELS, METHODS, SUPPORT_BOT, VIDEO_STATUSES
from utils import date, esc, fmt, level_info, min_total, min_video, money, top_rewards


def comments_min() -> int:
    return int(db.num("comments_min"))


def welcome(name: str) -> str:
    return (
        f"💙 <b>Привет, {esc(name)}!</b>\n\n"
        f"Продвигай <b>{CHANNEL}</b> в TikTok:\n"
        "🎬 за видео платим деньги\n"
        "💬 за комментарии начисляем баланс, его можно обменять на Telegram-аккаунты"
    )


APPLY = "📝 <b>Подать заявку</b>\n\nЗа что подаёшь?"

INFO = "ℹ️ <b>Информация</b>\n\nВыбери, что посмотреть 👇"


# ---------- правила ----------

COMMENT_RULES = [
    f"В комментарии обязательно упоминание <b>{CHANNEL}</b> (именно с @, просто текст «xazura shop» не считается)",
    "Только один комментарий на одно видео. На ответы («В ответ на комментарий») ограничений нет, их можно писать сколько угодно",
    "Меняй текст комментария каждые 3 комментария",
    f"Обязательно лайкай свои и чужие комментарии про {CHANNEL}",
    "Не спамь, делай перерывы между комментариями",
    "Тексты можно использовать и как ответ на видео, и как ответ на комментарий",
    "На каждый комментарий отдельный скриншот, на нём видны твой ник и текст комментария",
    "Плюс одна запись экрана истории комментариев (Профиль → Настройки → Центр активности → История комментариев)",
    "Один и тот же скриншот дважды не засчитывается",
    "Нарушение хотя бы одного пункта: заявка будет отклонена",
]

REFUSE_RULES = [
    "Накрутка, TikTok Ads и любые «серые» методы",
    "Чужие или украденные видео и скриншоты",
    "Упоминание или прямой текст про РФ",
    "Мат в видео или комментариях",
    "Видео или комментарии, которые портят репутацию шопа",
]

PAYOUT_RULES = [
    "За одно видео платим только один раз",
    "Пока заявка на проверке, новую того же типа подать нельзя",
    "Заявки проверяем вручную. Можем запросить дополнительную статистику",
    "За нарушения отказываем, за повторные выдаём бан в боте",
]


def _numbered(rules: list[str], start: int) -> str:
    return "\n".join(f"{i}. {rule}" for i, rule in enumerate(rules, start))


def comment_rules(start: int = 1) -> str:
    return _numbered(COMMENT_RULES, start)


def offers_lines(offers) -> list[str]:
    return [f"⚪️ {o['comments']} комментариев: {esc(o['title'])}" for o in offers]


def rates_text(offers) -> str:
    lines = [
        "💰 <b>Тарифы</b>",
        "",
        "🎬 <b>Видео</b>",
        f"🔹 <b>{fmt(db.num('rate'))} ₽</b> за 1 000 просмотров",
        f"📊 Видео учитывается от <b>{fmt(min_video())}</b> просм., выплата от <b>{fmt(min_total())}</b> суммарно",
        "💳 Рубли (карта / СБП), USDT, Telegram Stars",
        f"Курс: 1$ = {fmt(db.num('rub_per_usd'))} ₽ · 1⭐ = {fmt(db.num('rub_per_star'))} ₽",
        "",
        "<b>🏅 Уровни</b> (бонус к ставке за видео)",
    ]
    for threshold, name, bonus in LEVELS:
        cond = "старт" if threshold == 0 else f"от {fmt(threshold)} ₽"
        lines.append(f"{name}: {cond} · <b>+{bonus}%</b>")
    lines += [
        "",
        "💬 <b>Комментарии</b>",
        f"Заявка от <b>{comments_min()}</b> комментариев. Одобренные комментарии копятся на балансе.",
        "Обмен на Telegram-аккаунты:",
    ]
    lines += offers_lines(offers) or ["Сейчас аккаунтов нет в наличии."]
    return "\n".join(lines)


def rules_text() -> str:
    n = 14 + len(COMMENT_RULES)
    return f"""📜 <b>Правила</b>

<b>🎬 Видео</b>
1. Ролик посвящён {CHANNEL}
2. {CHANNEL} упоминается в видео и в описании
3. Профиль оформлен по гайду: ник <code>тгк - {CHANNEL}</code> и аватарка шопа
4. Видео длится не меньше 7 секунд
5. Видео опубликовано не раньше, чем 7 дней назад
6. Только русскоязычный контент и аудитория
7. Видео со смыслом и нормального качества. Просто текст на фоне не принимается
8. Запись экрана только со смартфона или планшета. Съёмка экрана ПК/ноутбука не принимается
9. Видео учитывается от {fmt(min_video())} просм., выплата от {fmt(min_total())} суммарно

<b>📊 Статистика видео</b>
10. Не менее 15% зрителей досмотрели ролик до конца
11. Среднее время просмотра не меньше 20%
12. Из поиска пришло не больше 25% зрителей
13. Из Азии пришло не больше 40% просмотров

<b>💬 Комментарии</b>
{comment_rules(14)}

<b>⛔️ Отказ, если:</b>
{_numbered(REFUSE_RULES, n)}

<b>💸 Выплаты</b>
{_numbered(PAYOUT_RULES, n + len(REFUSE_RULES))}

<i>Вопросы: {SUPPORT_BOT}</i>"""


# ---------- профиль / заявки / топ ----------

def profile_text(user, paid: int, accounts_got: int) -> str:
    name, bonus, nxt = level_info(user["total_paid"])
    requisites = (
        f"{METHODS[user['method']]}: <code>{esc(user['requisites'])}</code>" if user["method"] else "не указаны"
    )
    lines = [
        "👤 <b>Профиль</b>",
        "",
        f"🆔 <code>{user['id']}</code>",
        f"💳 Реквизиты: {requisites}",
        "",
        f"💬 Баланс комментариев: <b>{fmt(user['comment_balance'])}</b>",
        f"🎁 Получено аккаунтов: <b>{accounts_got}</b>",
        f"💰 Получено за видео: <b>{fmt(user['total_paid'])} ₽</b> ({paid} выплат)",
        f"🏅 Уровень: <b>{name}</b> (+{bonus}% к ставке)",
    ]
    if nxt:
        lines.append(f"📈 До уровня {nxt[1]}: ещё {fmt(nxt[0] - user['total_paid'])} ₽")
    if user["ref_comments"] or user["ref_rub"]:
        lines.append(f"👥 С друзей: {fmt(user['ref_comments'])} 💬 и {fmt(user['ref_rub'])} ₽")
    return "\n".join(lines)


def my_apps_text(videos, comments, exchanges) -> str:
    if not videos and not comments and not exchanges:
        return "📋 <b>Мои заявки</b>\n\nЗаявок пока нет. Жми «📝 Подать заявку» 🚀"
    lines = ["📋 <b>Мои заявки</b>", ""]
    if videos:
        lines.append("🎬 <b>За видео</b>")
        for app in videos:
            if app["note"]:
                what = f"{money(app['amount_rub'])} · {esc(app['note'])}"
            elif app["amount_rub"]:
                what = f"{money(app['amount_rub'])} · {fmt(app['total_views'])} 👁"
            else:
                what = "сумма после проверки статистики"
            lines.append(
                f"#{app['id']} · {date(app['created_at'])} · {VIDEO_STATUSES[app['status']]}\n   {what}"
            )
            if app["status"] == "rejected" and app["reason"]:
                lines.append(f"   📝 {esc(app['reason'][:200])}")
        lines.append("")
    if comments:
        lines.append("💬 <b>За комментарии</b>")
        for app in comments:
            got = f" · +{app['credited']} 💬" if app["status"] == "approved" else ""
            lines.append(
                f"#К{app['id']} · {date(app['created_at'])} · {COMMENT_STATUSES[app['status']]}\n"
                f"   {app['count']} скриншотов{got}"
            )
            if app["status"] == "rejected" and app["reason"]:
                lines.append(f"   📝 {esc(app['reason'][:200])}")
        lines.append("")
    if exchanges:
        lines.append("🔄 <b>Обмены</b>")
        for ex in exchanges:
            lines.append(
                f"#О{ex['id']} · {date(ex['created_at'])} · {EXCHANGE_STATUSES[ex['status']]}\n"
                f"   {esc(ex['offer_title'])} · {fmt(ex['cost'])} 💬"
            )
    return "\n".join(lines).strip()


MEDALS = ["🥇", "🥈", "🥉"]


def _place(i: int) -> str:
    return MEDALS[i - 1] if i <= len(MEDALS) else f"{i}."


def top_text(rows, my_id: int, label: str) -> str:
    lines = [f"🏆 <b>Топ креаторов: {label}</b> (по выплатам за видео)", ""]
    if not rows:
        lines.append("Пока пусто. Стань первым! 💙")
    for i, row in enumerate(rows[:10], 1):
        lines.append(f"{_place(i)} {esc(row['full_name'] or 'Креатор')}: <b>{fmt(row['total'])} ₽</b>")
    my_place = next((i + 1 for i, r in enumerate(rows) if r["user_id"] == my_id), None)
    lines += ["", f"📍 Твоё место: <b>{my_place}</b>" if my_place else "📍 Тебя пока нет в топе"]
    rewards = [(i, r) for i, r in enumerate(top_rewards(), 1) if r > 0]
    if rewards:
        lines += ["", "🎁 <b>Бонусы по итогам месяца</b> (начисляются автоматически 1-го числа)"]
        lines += [f"{_place(i)} {i} место: +{fmt(r)} ₽" for i, r in rewards]
    return "\n".join(lines)


def top_winner(place: int, label: str, amount: int) -> str:
    return (
        f"{_place(place)} <b>Ты занял {place} место в топе за {label}!</b>\n\n"
        f"Бонус <b>+{fmt(amount)} ₽</b> уже в очереди на выплату на твои реквизиты. Так держать 💙"
    )


def top_results(label: str, winners) -> str:
    lines = [f"🏆 <b>Итоги топа за {label}</b>", ""]
    lines += [f"{_place(p)} {esc(row['full_name'] or 'Креатор')}: +{fmt(amount)} ₽" for p, row, amount in winners]
    lines += ["", "Новый месяц, новый топ. Подавай заявки и забирай бонус 🚀"]
    return "\n".join(lines)


def top_awarded_admin(label: str, winners) -> str:
    lines = [f"🏆 <b>Бонусы топа за {label} начислены</b>", ""]
    lines += [f"{p} место: {esc(row['full_name'] or 'Креатор')} (<code>{row['user_id']}</code>), {fmt(amount)} ₽"
              for p, row, amount in winners]
    lines += ["", "Они в списке «💸 К оплате»."]
    return "\n".join(lines)


def admin_reminder(counts: dict[str, int], hours: int) -> str:
    lines = [f"⏰ <b>Заявки ждут проверки больше {hours} ч</b>", ""]
    names = {"video": "🎬 Видео", "comments": "💬 Комментарии", "exchanges": "🎁 Выдача аккаунтов"}
    lines += [f"{names[k]}: <b>{v}</b>" for k, v in counts.items() if v]
    lines += ["", "Открыть: /admin"]
    return "\n".join(lines)


def nudge(days: int) -> str:
    return (
        f"👋 Давно не видели твоих заявок, уже {days} дней!\n\n"
        f"Сними видео или оставь комментарии про {CHANNEL} и подавай заявку 👇"
    )


# ---------- реферальная программа ----------

def referral_text(user, link: str, stats: dict, friends) -> str:
    bonus, c_pct, v_pct = int(db.num("ref_bonus")), int(db.num("ref_comments_pct")), int(db.num("ref_video_pct"))
    rules = []
    if bonus:
        rules.append(f"🎁 <b>+{bonus}</b> 💬, когда друг получит первую выплату или зачисление")
    if c_pct:
        rules.append(f"💬 <b>{c_pct}%</b> от каждого зачисления комментариев друга")
    if v_pct:
        rules.append(f"💸 <b>{v_pct}%</b> от каждой выплаты другу за видео "
                     f"(рубли копятся и выплачиваются от {fmt(db.num('ref_min_payout'))} ₽)")
    lines = ["👥 <b>Приглашай друзей и зарабатывай</b>", "", "Ты получаешь, а у друга ничего не списывается:"]
    lines += rules or ["Сейчас бонусы за друзей выключены."]
    lines += [
        "",
        f"🔗 Твоя ссылка:\n<code>{link}</code>",
        "",
        f"👥 Приглашено: <b>{stats['invited']}</b> · уже получили выплату: <b>{stats['active']}</b>",
        f"💬 Заработано комментариев: <b>{fmt(user['ref_comments'])}</b>",
        f"💸 Заработано рублей: <b>{fmt(user['ref_rub'])} ₽</b>"
        + (f" (копится к выплате: {fmt(user['ref_rub_pending'])} ₽)" if user["ref_rub_pending"] else ""),
    ]
    if friends:
        lines += ["", "<b>Твои друзья:</b>"]
        lines += [f"{'✅' if f['ref_rewarded'] else '⏳'} {esc(f['full_name'] or 'Креатор')} · с {date(f['created_at'])}"
                  for f in friends]
        lines.append("⏳ ещё не получил выплату · ✅ уже приносит тебе доход")
    return "\n".join(lines)


def ref_joined(name: str) -> str:
    return f"👥 По твоей ссылке пришёл <b>{esc(name)}</b>! Бонусы начнут капать после его первой выплаты."


def ref_reward(name: str, reward: dict) -> str:
    parts = []
    if reward["first"]:
        parts.append(f"🎁 бонус за первую выплату: <b>+{reward['first']}</b> 💬")
    if reward["comments"]:
        parts.append(f"💬 доля от комментариев: <b>+{reward['comments']}</b> 💬")
    if reward["rub"]:
        parts.append(f"💸 доля от выплаты: <b>+{fmt(reward['rub'])} ₽</b>")
    text = f"👥 <b>{esc(name)}</b> получил выплату по твоему приглашению!\n\n" + "\n".join(parts)
    text += f"\n\n💬 Баланс: <b>{fmt(reward['balance'])}</b>"
    if reward["payout"]:
        text += f"\n💸 Набралось <b>{fmt(reward['payout'])} ₽</b> реферальных, они уже в очереди на выплату."
    return text


def no_phone_admin(ex_id: int, item_id: int) -> str:
    return (
        f"⚠️ <b>Обмен #О{ex_id}: аккаунт куплен, но LOLZ не отдал номер</b>\n\n"
        f"Лот на LOLZ: <code>{item_id}</code>. Найди номер в покупках LOLZ, отправь креатору "
        "и нажми «✅ Аккаунт выдан» в карточке ниже."
    )


def stuck_exchanges(count: int) -> str:
    return (
        f"⚠️ <b>Бот перезапустился посреди покупки аккаунтов: {count} шт.</b>\n\n"
        "Аккаунт мог купиться на LOLZ, а мог и нет. Проверь покупки на LOLZ и разбери обмены в "
        "/admin → «🎁 Выдать аккаунты»: «Аккаунт выдан» или «Отменить и вернуть»."
    )


# ---------- заявка за видео ----------

REQUISITES_PROMPT = {
    "rub": "💳 Отправь номер карты или телефон для СБП и название банка.\n"
           "Пример: <code>+79001234567 Т-Банк</code>",
    "crypto": "🪙 Отправь адрес кошелька USDT и сеть.\n"
              "Пример: <code>TRC20 TXyz...</code> или <code>TON UQ...</code>",
    "stars": "⭐ Отправь @username, куда прислать звёзды.",
}

VIDEO_PROFILE = (
    "🎬 <b>Заявка за видео</b>\n\n"
    "<b>Шаг 1 из 4.</b> Отправь ссылку на свой TikTok-аккаунт.\n\n"
    "Пример: <code>https://www.tiktok.com/@username</code>"
)

VIDEO_PROFILE_BAD = (
    "⚠️ Это не похоже на ссылку на аккаунт. Нужна ссылка на профиль, не на видео.\n"
    "Пример: <code>https://www.tiktok.com/@username</code>"
)


def video_links(profile: str) -> str:
    return (
        f"✅ Аккаунт: <b>@{esc(profile)}</b>\n\n"
        "<b>Шаг 2 из 4.</b> Отправь ссылку на видео.\n"
        "Если видео несколько, отправь их одним сообщением, каждое с новой строки.\n\n"
        f"📌 Учитываются видео от <b>{fmt(min_video())}</b> просмотров, выплата от <b>{fmt(min_total())}</b> суммарно."
    )


def videos_list(videos: list[dict], problems: list[str]) -> str:
    lines = []
    if problems:
        lines += ["⚠️ <b>Не принял:</b>"] + [f"• {p}" for p in problems] + [""]
    if not videos:
        lines.append("Пока ни одного видео. Отправь ссылки на видео.")
        return "\n".join(lines)
    lines.append(f"🎬 <b>Видео в заявке: {len(videos)}</b>")
    lines += [f"{i}. {esc(v['url'])}" for i, v in enumerate(videos, 1)]
    lines.append("\nМожешь дослать ещё ссылки или жми «➡️ Дальше».")
    return "\n".join(lines)


VIDEO_PROOF = (
    "📹 <b>Шаг 3 из 4. Запись экрана</b>\n\n"
    "Сними <b>одну запись экрана</b> с телефона и покажи в ней по порядку:\n\n"
    "1️⃣ <b>Статистику видео</b>: все вкладки с самого верха до низа, включая «Обзор», «Зрители», "
    "«Вовлечённость» и далее. Если видео несколько, покажи каждое\n"
    "2️⃣ Статистику <b>«Общее»</b>, где кнопка «Хотите больше трафика?», полностью\n"
    "3️⃣ <b>Просмотры у всех видео</b>: Комментарии → Просмотры, пролистай до самого конца\n"
    "4️⃣ <b>Профиль → Настройки и конфиденциальность → Центр активности → История комментариев</b>\n\n"
    "Листай медленно, ничего не обрезай. Отправь запись и нажми «➡️ Далее»."
)

PROOF_NOT_VIDEO = "🎥 Нужна именно <b>видеозапись экрана</b>. Отправь видео или нажми «➡️ Далее»."


def proofs_received(n: int) -> str:
    return f"✅ Запись принята ({n} шт.). Если запись не влезла в одно видео, дошли продолжение, или жми «➡️ Далее»."


def video_method() -> str:
    return (
        "💳 <b>Шаг 4 из 4. В чём получить оплату?</b>\n\n"
        f"Курс: 1$ = {fmt(db.num('rub_per_usd'))} ₽ · 1⭐ = {fmt(db.num('rub_per_star'))} ₽"
    )


def video_confirm(profile: str, videos: list[dict], proofs: int, method: str, requisites: str) -> str:
    return (
        "📋 <b>Проверь заявку</b>\n\n"
        f"🎵 Аккаунт: <b>@{esc(profile)}</b>\n"
        f"🎬 Видео: <b>{len(videos)}</b>\n"
        f"📹 Записей экрана: <b>{proofs}</b>\n"
        f"{METHODS[method]}: <code>{esc(requisites)}</code>\n\n"
        f"💰 Сумму посчитаем по статистике: <b>{fmt(db.num('rate'))} ₽</b> за 1 000 просмотров + бонус уровня."
    )


def video_sent(app_id: int) -> str:
    return f"✅ <b>Заявка #{app_id} отправлена!</b>\n\nРезультат придёт сюда. Статус: «📋 Мои заявки»."


def video_approved(app) -> str:
    return (
        f"👍 <b>Заявка #{app['id']} одобрена!</b>\n\n"
        f"Сумма: <b>{money(app['amount_rub'], app['method'])}</b>\n"
        "Скоро отправим выплату."
    )


def video_paid(app) -> str:
    return (
        f"💸 <b>Выплата по заявке #{app['id']} отправлена!</b>\n\n"
        f"Сумма: <b>{money(app['amount_rub'], app['method'])}</b>\n"
        f"{METHODS[app['method']]}: <code>{esc(app['requisites'])}</code>\n\n"
        "Спасибо за работу 💙"
    )


def level_up(name: str, bonus: int) -> str:
    return f"🎉 <b>Новый уровень: {name}!</b>\n\nТеперь ты получаешь <b>+{bonus}%</b> к ставке."


def rejected(code: str, reason: str) -> str:
    text = f"❌ <b>Заявка {code} отклонена</b>"
    if reason:
        text += f"\n\n📝 Причина: {esc(reason)}"
    return text + f"\n\nЕсли не согласен, напиши в {SUPPORT_BOT}"


# ---------- заявка за комментарии ----------

HASHTAGS = ["#физ", "#тгаккаунт", "#вирт", "#второйаккаунт", "#тгакки", "#физномер", "#анонимныйномер"]

COMMENT_EXAMPLES = [
    f"просто красавчики, спс за физак {CHANNEL}",
    f"лучшие {CHANNEL}",
    f"спасибо {CHANNEL}",
    f"легенды, +1 топ {CHANNEL}",
    f"{CHANNEL} всё же будет лучше",
    f"{CHANNEL} всё чётко",
    f"{CHANNEL} >>>",
    f"прикиньте, {CHANNEL} подарил",
    f"советую кста {CHANNEL}",
    f"у меня от {CHANNEL}, всё норм",
    f"не благодарите {CHANNEL}",
    f"у меня тоже есть {CHANNEL}",
    f"люблю их {CHANNEL} ❤️",
    f"видел тут {CHANNEL}",
    f"кому {CHANNEL}?",
    f"{CHANNEL} у них",
    f"от {CHANNEL}, спасибо",
    f"физа {CHANNEL}",
]


def comments_start() -> str:
    tags = " ".join(f"<code>{t}</code>" for t in HASHTAGS)
    return (
        "💬 <b>Заявка за комментарии</b>\n\n"
        "<b>Как это работает</b>\n"
        f"1. В поиске TikTok вбей любой хештег: {tags}\n"
        f"2. Пиши комментарии с <b>{CHANNEL}</b> под видео, лучше под свежими\n"
        "3. Делай скриншот каждого комментария\n"
        f"4. Отправь сюда минимум <b>{comments_min()}</b> скриншотов (можно пачками до 10), "
        "затем запись истории комментариев\n\n"
        "Тексты бери в «📝 Примеры комментариев». Одобренные комментарии зачислим на баланс, "
        "их можно обменять в «🔄 Обменять комментарии».\n\n"
        f"<b>📌 Правила</b>\n<blockquote expandable>{comment_rules()}</blockquote>"
    )


SHOT_NOT_IMAGE = "📸 Нужен именно <b>скриншот</b> (фото). Отправь картинку."


def shots_progress(count: int, need: int, dupes: int) -> str:
    text = f"📸 Принято скриншотов: <b>{count}</b>"
    if dupes:
        text += f"\n⚠️ Повторы не засчитаны: {dupes}"
    if count >= need:
        text += "\n\nМожешь дослать ещё или жми «➡️ Дальше»."
    else:
        text += f"\nДо минимума: ещё <b>{need - count}</b>"
    return text


COMMENT_RECORD = (
    "📹 <b>Последний шаг: запись экрана</b>\n\n"
    "Профиль → Настройки и конфиденциальность → Центр активности → История комментариев.\n"
    "Запиши экран <b>с телефона</b> и медленно пролистай историю, чтобы были видны все комментарии из заявки.\n\n"
    "Отправь видео и нажми «✅ Отправить заявку»."
)

RECORD_NOT_VIDEO = "🎥 Нужна именно <b>видеозапись экрана</b> истории комментариев."


def records_received(n: int) -> str:
    return f"✅ Принято записей: <b>{n}</b>. Отправь ещё или нажми «✅ Отправить заявку»."


def examples(custom: str | None) -> str:
    """custom — примеры, заданные админом (по строке на пример); иначе встроенные."""
    # Номера «1.» из вставленного списка убираем, чтобы они не попадали в копируемый текст
    items = [re.sub(r"^\d+[.)]\s*", "", line.strip()) for line in custom.splitlines() if line.strip()] \
        if custom else COMMENT_EXAMPLES
    lines = "\n".join(f"<code>{esc(item)}</code>" for item in items)
    return (
        f"📝 <b>Примеры комментариев</b>\n\n{lines}\n\n"
        "<i>Нажми на текст, чтобы скопировать. Меняй текст каждые 3 комментария, "
        "одинаковые TikTok удаляет.</i>"
    )


def comment_sent(app_id: int, count: int) -> str:
    return (
        f"✅ <b>Заявка #К{app_id} отправлена!</b> ({count} скриншотов)\n\n"
        "После проверки комментарии зачислим на баланс. Статус: «📋 Мои заявки»."
    )


def comment_credited(app, balance: int) -> str:
    part = f" из {app['count']}" if app["credited"] < app["count"] else ""
    return (
        f"✅ <b>Заявка #К{app['id']} одобрена!</b>\n\n"
        f"Зачислено: <b>+{app['credited']}{part}</b> 💬\n"
        f"Баланс: <b>{fmt(balance)}</b> 💬\n\n"
        "Обменять на аккаунт: «🔄 Обменять комментарии»."
    )


# ---------- обмен ----------

def exchange_text(offers, balance: int) -> str:
    lines = [
        "🔄 <b>Обмен комментариев</b>",
        "",
        f"💬 Твой баланс: <b>{fmt(balance)}</b>",
        "",
    ]
    if not offers:
        lines.append("Сейчас аккаунтов нет в наличии.")
    else:
        lines.append("Выбери аккаунт (✅ хватает, 🔒 пока не хватает):")
    return "\n".join(lines)


def exchange_confirm(title: str, cost: int, balance: int) -> str:
    return (
        f"🎁 <b>{esc(title)}</b>\n\n"
        f"Спишем: <b>{fmt(cost)}</b> 💬\n"
        f"Останется: <b>{fmt(balance - cost)}</b> 💬\n\n"
        "Обменять?"
    )


def exchange_sent(ex_id: int, title: str) -> str:
    return (
        f"✅ <b>Обмен #О{ex_id} оформлен!</b>\n\n"
        f"🎁 {esc(title)}\n"
        "Аккаунт пришлём в ближайшее время, уведомим здесь."
    )


def exchange_done(ex) -> str:
    return (
        f"🎁 <b>Аккаунт по обмену #О{ex['id']} выдан!</b>\n\n"
        f"{esc(ex['offer_title'])}\n\nСпасибо за работу 💙"
    )


def exchange_cancelled(ex) -> str:
    return (
        f"↩️ <b>Обмен #О{ex['id']} отменён</b>\n\n"
        f"{fmt(ex['cost'])} 💬 вернули на баланс. Вопросы: {SUPPORT_BOT}"
    )


# ---------- автовыдача аккаунтов ----------

def exchange_buying(title: str) -> str:
    return f"⏳ <b>Покупаю аккаунт {esc(title)}…</b>\n\nОбычно это занимает несколько секунд."


def account_view(ex, fresh: bool = False) -> str:
    head = f"🎉 <b>Аккаунт твой!</b> Списано {fmt(ex['cost'])} 💬\n\n" if fresh else ""
    return (
        f"{head}📱 <b><code>{esc(ex['phone'])}</code></b>\n"
        f"🌍 {esc(ex['offer_title'])}\n"
        f"🗓 Получен {date(ex['decided_at'])}\n\n"
        "🔐 <b>Как войти</b>\n"
        "1️⃣ Введи номер в приложении Telegram.\n"
        "2️⃣ Нажми «🔑 Получить код».\n\n"
        f"Если что-то не так, напиши в {SUPPORT_BOT} и укажи номер."
    )


def accounts_list(accounts) -> str:
    if not accounts:
        return "📱 <b>Мои аккаунты</b>\n\nЗдесь появятся аккаунты, полученные за комментарии."
    return "📱 <b>Мои аккаунты</b>\n\nВыбери номер, чтобы получить код входа 👇"


def login_code(phone: str, code: str) -> str:
    return f"🔑 <b>Код входа</b>\n\n📱 Номер: <code>{esc(phone)}</code>\n🔢 Код: <code>{esc(code)}</code>"


def exchange_failed(ex, balance: int) -> str:
    return (
        f"😔 <b>Сейчас нет аккаунтов {esc(ex['offer_title'])}</b>\n\n"
        f"{fmt(ex['cost'])} 💬 вернули на баланс (сейчас {fmt(balance)} 💬).\n"
        "Попробуй другую страну или загляни позже."
    )


def _who(user) -> str:
    return esc(user["full_name"] or "Креатор") + (f" (@{esc(user['username'])})" if user["username"] else "")


def auto_issued_admin(ex, user) -> str:
    return (
        f"🎁 <b>Автовыдача #О{ex['id']}</b>\n"
        f"{_who(user)} · <code>{user['id']}</code>\n"
        f"{esc(ex['offer_title'])}: <code>{esc(ex['phone'])}</code>\n"
        f"Списано {fmt(ex['cost'])} 💬 · закупка на LOLZ {fmt(round(ex['buy_price'] or 0, 2))} ₽"
    )


def auto_failed_admin(ex, user, no_money: bool, error: str | None) -> str:
    if no_money:
        reason = "🚨 <b>На LOLZ закончились деньги!</b>"
    elif error:
        reason = f"🚨 Ошибка LOLZ:\n<code>{esc(error[:300])}</code>"
    else:
        reason = "⚠️ Нет подходящих лотов. Проверь потолок закупки в «🏷 Прайс обмена»."
    return (
        f"{reason}\n\nНе выдался аккаунт {esc(ex['offer_title'])} креатору {_who(user)}. "
        f"Обмен #О{ex['id']} отменён, {fmt(ex['cost'])} 💬 возвращены."
    )
