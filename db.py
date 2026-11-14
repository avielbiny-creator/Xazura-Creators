import asyncio
import json
import time

import aiosqlite

from config import DB_PATH, DEFAULT_OFFERS, DEFAULT_SETTINGS

_db: aiosqlite.Connection | None = None
_settings: dict[str, str] = {}
# Одно соединение на весь бот: записи из разных обработчиков не должны перемешиваться внутри транзакции
_lock = asyncio.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    full_name TEXT,
    method TEXT,
    requisites TEXT,
    total_paid INTEGER NOT NULL DEFAULT 0,
    comment_balance INTEGER NOT NULL DEFAULT 0,
    referrer_id INTEGER,
    ref_rewarded INTEGER NOT NULL DEFAULT 0,
    ref_comments INTEGER NOT NULL DEFAULT 0,
    ref_rub INTEGER NOT NULL DEFAULT 0,
    ref_rub_pending INTEGER NOT NULL DEFAULT 0,
    last_nudge INTEGER,
    banned INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS video_apps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    total_views INTEGER NOT NULL,
    rate REAL NOT NULL,
    bonus_pct INTEGER NOT NULL DEFAULT 0,
    amount_rub INTEGER NOT NULL,
    method TEXT NOT NULL,
    requisites TEXT NOT NULL,
    proofs TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reason TEXT,
    admin_name TEXT,
    note TEXT,
    profile TEXT,
    created_at INTEGER NOT NULL,
    approved_at INTEGER,
    paid_at INTEGER
);
CREATE TABLE IF NOT EXISTS app_videos (
    app_id INTEGER NOT NULL,
    video_key TEXT NOT NULL,
    url TEXT NOT NULL,
    views INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_app_videos_key ON app_videos(video_key);
CREATE TABLE IF NOT EXISTS comment_apps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    count INTEGER NOT NULL,
    credited INTEGER,
    proofs TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    reason TEXT,
    admin_name TEXT,
    created_at INTEGER NOT NULL,
    decided_at INTEGER
);
CREATE TABLE IF NOT EXISTS comment_shots (
    app_id INTEGER NOT NULL,
    unique_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comment_shots_uid ON comment_shots(unique_id);
CREATE TABLE IF NOT EXISTS exchanges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    offer_title TEXT NOT NULL,
    cost INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    admin_name TEXT,
    item_id INTEGER,
    phone TEXT,
    buy_price REAL,
    created_at INTEGER NOT NULL,
    decided_at INTEGER
);
CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    comments INTEGER NOT NULL,
    title TEXT NOT NULL,
    code TEXT,
    max_price REAL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fsm (key TEXT PRIMARY KEY, state TEXT, data TEXT NOT NULL DEFAULT '{}');
"""


# Колонки, добавленные после первого запуска: в старую базу их докидываем при старте
MIGRATIONS = [
    ("users", "referrer_id", "INTEGER"),
    ("users", "ref_rewarded", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "last_nudge", "INTEGER"),
    ("users", "ref_comments", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "ref_rub", "INTEGER NOT NULL DEFAULT 0"),
    ("users", "ref_rub_pending", "INTEGER NOT NULL DEFAULT 0"),
    ("video_apps", "note", "TEXT"),
    ("video_apps", "profile", "TEXT"),
    ("offers", "code", "TEXT"),
    ("offers", "max_price", "REAL"),
    ("exchanges", "item_id", "INTEGER"),
    ("exchanges", "phone", "TEXT"),
    ("exchanges", "buy_price", "REAL"),
]


async def init() -> int:
    """Возвращает, сколько обменов зависло в покупке (бот упал посреди покупки на LOLZ)."""
    global _db
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(SCHEMA)
    for table, column, ddl in MIGRATIONS:
        if column not in [r["name"] for r in await _all(f"PRAGMA table_info({table})")]:
            await _db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    for key, value in DEFAULT_SETTINGS.items():
        await _db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)", (key, str(value)))
    if await _one("SELECT 1 FROM settings WHERE key='offers_seeded'") is None:
        await _db.executemany("INSERT INTO offers(comments, title, code, max_price) VALUES(?, ?, ?, ?)", DEFAULT_OFFERS)
        await _db.execute("INSERT INTO settings(key, value) VALUES('offers_seeded', '1')")
    # Прайс из старой версии без кодов стран: дописываем их, чтобы работала автовыдача
    await _db.executemany(
        "UPDATE offers SET code=?, max_price=? WHERE title=? AND code IS NULL",
        [(code, price, title) for _, title, code, price in DEFAULT_OFFERS],
    )
    # Неизвестно, купился ли аккаунт: отдаём такие обмены админу на ручную проверку
    stuck = await _db.execute("UPDATE exchanges SET status='pending' WHERE status='buying'")
    await _db.commit()
    for row in await _all("SELECT key, value FROM settings"):
        _settings[row["key"]] = row["value"]
    return stuck.rowcount


async def close():
    if _db:
        await _db.close()


async def _one(sql, args=()):
    async with _db.execute(sql, args) as cur:
        return await cur.fetchone()


async def _all(sql, args=()):
    async with _db.execute(sql, args) as cur:
        return await cur.fetchall()


async def _run(sql, args=()):
    async with _lock:
        cur = await _db.execute(sql, args)
        await _db.commit()
        return cur


# ---------- settings ----------

def num(key: str) -> float:
    return float(_settings[key])


def text(key: str) -> str:
    return _settings[key]


def get_setting(key: str) -> str | None:
    return _settings.get(key)


def all_settings() -> dict[str, str]:
    return dict(_settings)


async def set_setting(key: str, value: str):
    await _run("INSERT OR REPLACE INTO settings(key, value) VALUES(?, ?)", (key, value))
    _settings[key] = value


async def del_setting(key: str):
    await _run("DELETE FROM settings WHERE key=?", (key,))
    _settings.pop(key, None)


# ---------- черновики (состояние диалогов) ----------

async def fsm_get(key: str):
    return await _one("SELECT state, data FROM fsm WHERE key=?", (key,))


async def fsm_set(key: str, state: str | None, data: dict):
    if state is None and not data:
        await _run("DELETE FROM fsm WHERE key=?", (key,))
    else:
        await _run(
            "INSERT INTO fsm(key, state, data) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET state=excluded.state, data=excluded.data",
            (key, state, json.dumps(data, ensure_ascii=False)),
        )


# ---------- users ----------

async def upsert_user(user_id: int, username: str | None, full_name: str):
    await _run(
        "INSERT INTO users(id, username, full_name, created_at) VALUES(?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET username=excluded.username, full_name=excluded.full_name",
        (user_id, username, full_name, int(time.time())),
    )


async def get_user(user_id: int):
    return await _one("SELECT * FROM users WHERE id=?", (user_id,))


async def find_user(query: str):
    query = query.strip().lstrip("@")
    if query.isdigit():
        return await get_user(int(query))
    return await _one("SELECT * FROM users WHERE lower(username)=lower(?)", (query,))


async def set_requisites(user_id: int, method: str, requisites: str):
    await _run("UPDATE users SET method=?, requisites=? WHERE id=?", (method, requisites, user_id))


async def set_banned(user_id: int, banned: bool):
    await _run("UPDATE users SET banned=? WHERE id=?", (int(banned), user_id))


async def add_total_paid(user_id: int, delta: int):
    await _run("UPDATE users SET total_paid=MAX(0, total_paid + ?) WHERE id=?", (delta, user_id))


async def all_user_ids() -> list[int]:
    return [r["id"] for r in await _all("SELECT id FROM users WHERE banned=0")]


# ---------- заявки за видео ----------

async def pending_video_of(user_id: int):
    return await _one("SELECT * FROM video_apps WHERE user_id=? AND status='pending'", (user_id,))


async def video_taken(video_key: str) -> bool:
    row = await _one(
        "SELECT 1 FROM app_videos v JOIN video_apps a ON a.id=v.app_id "
        "WHERE v.video_key=? AND a.status != 'rejected'",
        (video_key,),
    )
    return row is not None


async def create_video_app(user_id, profile, videos, rate, bonus_pct, method, requisites, proofs) -> int:
    """Просмотры и сумму ставит админ после проверки статистики."""
    async with _lock:
        cur = await _db.execute(
            "INSERT INTO video_apps(user_id, profile, total_views, rate, bonus_pct, amount_rub, method, requisites, "
            "proofs, created_at) VALUES(?, ?, 0, ?, ?, 0, ?, ?, ?, ?)",
            (user_id, profile, rate, bonus_pct, method, requisites, json.dumps(proofs), int(time.time())),
        )
        app_id = cur.lastrowid
        await _db.executemany(
            "INSERT INTO app_videos(app_id, video_key, url, views) VALUES(?, ?, ?, 0)",
            [(app_id, v["key"], v["url"]) for v in videos],
        )
        await _db.commit()
        return app_id


async def set_video_views(app_id: int, views: int, amount: int) -> bool:
    cur = await _run(
        "UPDATE video_apps SET total_views=?, amount_rub=? WHERE id=? AND status IN ('pending', 'approved')",
        (views, amount, app_id),
    )
    return cur.rowcount > 0


async def get_video_app(app_id: int):
    return await _one("SELECT * FROM video_apps WHERE id=?", (app_id,))


async def get_app_videos(app_id: int):
    return await _all("SELECT url, views FROM app_videos WHERE app_id=?", (app_id,))


async def set_video_amount(app_id: int, amount: int) -> bool:
    cur = await _run(
        "UPDATE video_apps SET amount_rub=? WHERE id=? AND status IN ('pending', 'approved')", (amount, app_id)
    )
    return cur.rowcount > 0


async def approve_video(app_id: int, admin_name: str) -> bool:
    cur = await _run(
        "UPDATE video_apps SET status='approved', approved_at=?, admin_name=? WHERE id=? AND status='pending'",
        (int(time.time()), admin_name, app_id),
    )
    return cur.rowcount > 0


async def mark_video_paid(app_id: int, admin_name: str) -> bool:
    async with _lock:
        cur = await _db.execute(
            "UPDATE video_apps SET status='paid', paid_at=?, admin_name=? WHERE id=? AND status='approved'",
            (int(time.time()), admin_name, app_id),
        )
        if cur.rowcount == 0:
            return False
        await _db.execute(
            "UPDATE users SET total_paid = total_paid + (SELECT amount_rub FROM video_apps WHERE id=?) "
            "WHERE id=(SELECT user_id FROM video_apps WHERE id=?)",
            (app_id, app_id),
        )
        await _db.commit()
        return True


async def reject_video(app_id: int, reason: str, admin_name: str) -> bool:
    cur = await _run(
        "UPDATE video_apps SET status='rejected', reason=?, admin_name=? WHERE id=? AND status IN ('pending', 'approved')",
        (reason, admin_name, app_id),
    )
    return cur.rowcount > 0


async def video_apps_by_status(status: str):
    return await _all(
        "SELECT a.*, u.full_name, u.username FROM video_apps a JOIN users u ON u.id=a.user_id "
        "WHERE a.status=? ORDER BY a.id",
        (status,),
    )


async def video_history(user_id: int, limit: int = 10):
    return await _all("SELECT * FROM video_apps WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))


async def paid_count(user_id: int) -> int:
    row = await _one("SELECT COUNT(*) AS c FROM video_apps WHERE user_id=? AND status='paid'", (user_id,))
    return row["c"]


async def top_between(start: int, end: int):
    """Топ по выплатам за видео. Бонусы топа (note) в зачёт не идут."""
    return await _all(
        "SELECT a.user_id, u.full_name, SUM(a.amount_rub) AS total FROM video_apps a "
        "JOIN users u ON u.id=a.user_id WHERE a.status='paid' AND a.note IS NULL AND a.paid_at>=? AND a.paid_at<? "
        "GROUP BY a.user_id ORDER BY total DESC",
        (start, end),
    )


async def create_bonus_payout(user_id: int, amount: int, note: str) -> int:
    """Бонус без видео: сразу «одобрен», попадает в список «К оплате»."""
    user = await get_user(user_id)
    cur = await _run(
        "INSERT INTO video_apps(user_id, total_views, rate, amount_rub, method, requisites, proofs, status, note, "
        "admin_name, created_at, approved_at) VALUES(?, 0, 0, ?, ?, ?, '[]', 'approved', ?, 'бот', ?, ?)",
        (user_id, amount, user["method"] or "rub", user["requisites"] or "уточнить у креатора", note,
         int(time.time()), int(time.time())),
    )
    return cur.lastrowid


# ---------- заявки за комментарии ----------

async def pending_comment_of(user_id: int):
    return await _one("SELECT * FROM comment_apps WHERE user_id=? AND status='pending'", (user_id,))


async def shots_taken(unique_ids: list[str]) -> set[str]:
    if not unique_ids:
        return set()
    marks = ",".join("?" * len(unique_ids))
    rows = await _all(
        f"SELECT s.unique_id FROM comment_shots s JOIN comment_apps a ON a.id=s.app_id "
        f"WHERE a.status != 'rejected' AND s.unique_id IN ({marks})",
        unique_ids,
    )
    return {r["unique_id"] for r in rows}


async def create_comment_app(user_id: int, count: int, proofs: list[dict]) -> int:
    async with _lock:
        cur = await _db.execute(
            "INSERT INTO comment_apps(user_id, count, proofs, created_at) VALUES(?, ?, ?, ?)",
            (user_id, count, json.dumps(proofs), int(time.time())),
        )
        app_id = cur.lastrowid
        await _db.executemany(
            "INSERT INTO comment_shots(app_id, unique_id) VALUES(?, ?)",
            [(app_id, p["uid"]) for p in proofs if "uid" in p],
        )
        await _db.commit()
        return app_id


async def get_comment_app(app_id: int):
    return await _one("SELECT * FROM comment_apps WHERE id=?", (app_id,))


async def credit_comments(app_id: int, credited: int, admin_name: str) -> bool:
    """Одобряет заявку и зачисляет комментарии на баланс креатора."""
    async with _lock:
        cur = await _db.execute(
            "UPDATE comment_apps SET status='approved', credited=?, admin_name=?, decided_at=? "
            "WHERE id=? AND status='pending'",
            (credited, admin_name, int(time.time()), app_id),
        )
        if cur.rowcount == 0:
            return False
        await _db.execute(
            "UPDATE users SET comment_balance = comment_balance + ? "
            "WHERE id=(SELECT user_id FROM comment_apps WHERE id=?)",
            (credited, app_id),
        )
        await _db.commit()
        return True


async def reject_comments(app_id: int, reason: str, admin_name: str) -> bool:
    cur = await _run(
        "UPDATE comment_apps SET status='rejected', reason=?, admin_name=?, decided_at=? "
        "WHERE id=? AND status='pending'",
        (reason, admin_name, int(time.time()), app_id),
    )
    return cur.rowcount > 0


async def pending_comment_apps():
    return await _all(
        "SELECT a.*, u.full_name FROM comment_apps a JOIN users u ON u.id=a.user_id "
        "WHERE a.status='pending' ORDER BY a.id"
    )


async def comment_history(user_id: int, limit: int = 10):
    return await _all("SELECT * FROM comment_apps WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))


async def add_comment_balance(user_id: int, delta: int):
    await _run("UPDATE users SET comment_balance=MAX(0, comment_balance + ?) WHERE id=?", (delta, user_id))


# ---------- обмен комментариев на аккаунты ----------

async def create_exchange(user_id: int, offer_title: str, cost: int, status: str = "pending") -> int | None:
    """Списывает комментарии и создаёт обмен. None, если баланса не хватает.
    status='buying' — бот сам покупает аккаунт, такой обмен админ не видит и не может отменить."""
    async with _lock:
        cur = await _db.execute(
            "UPDATE users SET comment_balance = comment_balance - ? WHERE id=? AND comment_balance >= ?",
            (cost, user_id, cost),
        )
        if cur.rowcount == 0:
            return None
        cur = await _db.execute(
            "INSERT INTO exchanges(user_id, offer_title, cost, status, created_at) VALUES(?, ?, ?, ?, ?)",
            (user_id, offer_title, cost, status, int(time.time())),
        )
        await _db.commit()
        return cur.lastrowid


async def get_exchange(ex_id: int):
    return await _one("SELECT * FROM exchanges WHERE id=?", (ex_id,))


async def finish_exchange(ex_id: int, done: bool, admin_name: str, from_status: str = "pending") -> bool:
    """done=True: аккаунт выдан. done=False: отмена, комментарии возвращаются на баланс."""
    async with _lock:
        cur = await _db.execute(
            "UPDATE exchanges SET status=?, admin_name=?, decided_at=? WHERE id=? AND status=?",
            ("done" if done else "cancelled", admin_name, int(time.time()), ex_id, from_status),
        )
        if cur.rowcount == 0:
            return False
        if not done:
            await _db.execute(
                "UPDATE users SET comment_balance = comment_balance + (SELECT cost FROM exchanges WHERE id=?) "
                "WHERE id=(SELECT user_id FROM exchanges WHERE id=?)",
                (ex_id, ex_id),
            )
        await _db.commit()
        return True


async def complete_exchange(ex_id: int, item_id: int, phone: str, buy_price: float):
    """Аккаунт куплен на LOLZ и выдан креатору автоматически."""
    await _run(
        "UPDATE exchanges SET status='done', admin_name='автовыдача', item_id=?, phone=?, buy_price=?, decided_at=? "
        "WHERE id=? AND status='buying'",
        (item_id, phone, buy_price, int(time.time()), ex_id),
    )


async def exchange_to_admin(ex_id: int, item_id: int | None = None, buy_price: float | None = None):
    """Автопокупка не довела дело до конца: пусть админ проверит LOLZ и выдаст вручную."""
    await _run(
        "UPDATE exchanges SET status='pending', item_id=?, buy_price=? WHERE id=? AND status='buying'",
        (item_id, buy_price, ex_id),
    )


async def user_accounts(user_id: int):
    """Аккаунты, выданные автоматически: по ним можно запросить код входа."""
    return await _all(
        "SELECT * FROM exchanges WHERE user_id=? AND status='done' AND phone IS NOT NULL ORDER BY id DESC LIMIT 20",
        (user_id,),
    )


async def pending_exchanges():
    return await _all(
        "SELECT e.*, u.full_name FROM exchanges e JOIN users u ON u.id=e.user_id "
        "WHERE e.status='pending' ORDER BY e.id"
    )


async def exchange_history(user_id: int, limit: int = 10):
    return await _all("SELECT * FROM exchanges WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))


async def done_exchange_count(user_id: int) -> int:
    row = await _one("SELECT COUNT(*) AS c FROM exchanges WHERE user_id=? AND status='done'", (user_id,))
    return row["c"]


# ---------- прайс аккаунтов ----------

async def list_offers():
    return await _all("SELECT * FROM offers ORDER BY comments, id")


async def get_offer(offer_id: int):
    return await _one("SELECT * FROM offers WHERE id=?", (offer_id,))


async def add_offer(comments: int, title: str, code: str | None, max_price: float | None):
    await _run(
        "INSERT INTO offers(comments, title, code, max_price) VALUES(?, ?, ?, ?)", (comments, title, code, max_price)
    )


async def delete_offer(offer_id: int):
    await _run("DELETE FROM offers WHERE id=?", (offer_id,))


# ---------- рефералы ----------

async def set_referrer(user_id: int, referrer_id: int, joined_after: int) -> bool:
    """Привязывает пригласившего только к новому креатору и только один раз."""
    if user_id == referrer_id or not await get_user(referrer_id):
        return False
    cur = await _run(
        "UPDATE users SET referrer_id=? WHERE id=? AND referrer_id IS NULL AND created_at>=?",
        (referrer_id, user_id, joined_after),
    )
    return cur.rowcount > 0


async def reward_referrer(user_id: int, bonus: int, comments: int, rub: int, min_payout: int) -> dict | None:
    """Начисляет пригласившему его долю, когда приглашённый что-то получил.
    bonus — разовый бонус за первую выплату или зачисление (💬), comments и rub — процент от зачисления и выплаты.
    Рубли копятся и, набрав min_payout, уходят в «К оплате» отдельной выплатой."""
    async with _lock:
        row = await _one("SELECT referrer_id, ref_rewarded FROM users WHERE id=?", (user_id,))
        ref = await _one("SELECT * FROM users WHERE id=?", (row["referrer_id"],)) if row["referrer_id"] else None
        if not ref or ref["banned"]:
            return None
        first = bonus if not row["ref_rewarded"] else 0
        gained = first + comments
        if not gained and not rub:
            return None
        await _db.execute("UPDATE users SET ref_rewarded=1 WHERE id=?", (user_id,))
        pending = ref["ref_rub_pending"] + rub
        payout = pending if pending >= min_payout > 0 else 0
        await _db.execute(
            "UPDATE users SET comment_balance = comment_balance + ?, ref_comments = ref_comments + ?, "
            "ref_rub = ref_rub + ?, ref_rub_pending = ? WHERE id=?",
            (gained, gained, rub, pending - payout, ref["id"]),
        )
        if payout:
            await _db.execute(
                "INSERT INTO video_apps(user_id, total_views, rate, amount_rub, method, requisites, proofs, status, "
                "note, admin_name, created_at, approved_at) VALUES(?, 0, 0, ?, ?, ?, '[]', 'approved', ?, 'бот', ?, ?)",
                (ref["id"], payout, ref["method"] or "rub", ref["requisites"] or "уточнить у креатора",
                 "👥 Реферальные начисления", int(time.time()), int(time.time())),
            )
        await _db.commit()
        return {"referrer": ref["id"], "first": first, "comments": comments, "rub": rub, "payout": payout,
                "balance": ref["comment_balance"] + gained}


async def referral_stats(user_id: int) -> dict:
    row = await _one(
        "SELECT COUNT(*) AS c, COALESCE(SUM(ref_rewarded), 0) AS r FROM users WHERE referrer_id=?", (user_id,)
    )
    return {"invited": row["c"], "active": row["r"]}


async def referrals(user_id: int, limit: int = 10):
    return await _all(
        "SELECT full_name, ref_rewarded, created_at FROM users WHERE referrer_id=? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )


# ---------- напоминания ----------

async def stale_counts(older_than: int) -> dict[str, int]:
    """Сколько заявок ждут админа дольше заданного времени."""
    result = {}
    for key, sql in (
        ("video", "SELECT COUNT(*) AS c FROM video_apps WHERE status='pending' AND created_at<?"),
        ("comments", "SELECT COUNT(*) AS c FROM comment_apps WHERE status='pending' AND created_at<?"),
        ("exchanges", "SELECT COUNT(*) AS c FROM exchanges WHERE status='pending' AND created_at<?"),
    ):
        result[key] = (await _one(sql, (older_than,)))["c"]
    return result


async def users_to_nudge(inactive_since: int) -> list[int]:
    """Креаторы без заявок с inactive_since, которым ещё не напоминали за этот срок."""
    rows = await _all(
        "SELECT id FROM users u WHERE banned=0 AND created_at<:t AND (last_nudge IS NULL OR last_nudge<:t) "
        "AND NOT EXISTS (SELECT 1 FROM video_apps WHERE user_id=u.id AND created_at>=:t) "
        "AND NOT EXISTS (SELECT 1 FROM comment_apps WHERE user_id=u.id AND created_at>=:t)",
        {"t": inactive_since},
    )
    return [r["id"] for r in rows]


async def mark_nudged(user_id: int):
    await _run("UPDATE users SET last_nudge=? WHERE id=?", (int(time.time()), user_id))


# ---------- выгрузка ----------

async def export_rows(start: int, end: int) -> dict[str, list]:
    users = "u.full_name, u.username, u.id AS uid"
    return {
        "videos": await _all(
            f"SELECT a.*, {users} FROM video_apps a JOIN users u ON u.id=a.user_id "
            "WHERE a.status='paid' AND a.paid_at>=? AND a.paid_at<? ORDER BY a.paid_at",
            (start, end),
        ),
        "comments": await _all(
            f"SELECT a.*, {users} FROM comment_apps a JOIN users u ON u.id=a.user_id "
            "WHERE a.status='approved' AND a.decided_at>=? AND a.decided_at<? ORDER BY a.decided_at",
            (start, end),
        ),
        "exchanges": await _all(
            f"SELECT e.*, {users} FROM exchanges e JOIN users u ON u.id=e.user_id "
            "WHERE e.status='done' AND e.decided_at>=? AND e.decided_at<? ORDER BY e.decided_at",
            (start, end),
        ),
    }


# ---------- статистика ----------

async def stats(month_start: int) -> dict:
    one = _one
    users = await one("SELECT COUNT(*) AS c, COALESCE(SUM(banned), 0) AS b FROM users")
    pending = await one("SELECT COUNT(*) AS c FROM video_apps WHERE status='pending'")
    approved = await one("SELECT COUNT(*) AS c, COALESCE(SUM(amount_rub), 0) AS s FROM video_apps WHERE status='approved'")
    paid = await one("SELECT COUNT(*) AS c, COALESCE(SUM(amount_rub), 0) AS s FROM video_apps WHERE status='paid'")
    month = await one(
        "SELECT COUNT(*) AS c, COALESCE(SUM(amount_rub), 0) AS s FROM video_apps WHERE status='paid' AND paid_at>=?",
        (month_start,),
    )
    c_pending = await one("SELECT COUNT(*) AS c FROM comment_apps WHERE status='pending'")
    c_month = await one(
        "SELECT COALESCE(SUM(credited), 0) AS s FROM comment_apps WHERE status='approved' AND decided_at>=?",
        (month_start,),
    )
    balance = await one("SELECT COALESCE(SUM(comment_balance), 0) AS s FROM users")
    e_pending = await one("SELECT COUNT(*) AS c FROM exchanges WHERE status='pending'")
    e_month = await one("SELECT COUNT(*) AS c FROM exchanges WHERE status='done' AND decided_at>=?", (month_start,))
    e_done = await one("SELECT COUNT(*) AS c FROM exchanges WHERE status='done'")
    return {
        "users": users["c"], "banned": users["b"], "pending": pending["c"],
        "approved": approved["c"], "approved_sum": approved["s"],
        "paid": paid["c"], "paid_sum": paid["s"], "month": month["c"], "month_sum": month["s"],
        "c_pending": c_pending["c"], "c_month": c_month["s"], "balance": balance["s"],
        "e_pending": e_pending["c"], "e_month": e_month["c"], "e_done": e_done["c"],
    }
