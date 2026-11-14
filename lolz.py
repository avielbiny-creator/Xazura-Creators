"""Покупка Telegram-аккаунтов на LOLZ при обмене комментариев. Логика та же, что в магазине XazuraShop."""
import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger(__name__)

PAGE_SIZE = 40  # лотов на странице поиска LOLZ
SEARCH_PAGES = 8  # максимум запросов на один поиск
MAX_SEARCHES = 3  # сколько раз переискиваем лоты, если первые не купились
CODE_COOLDOWN = 5  # секунд между запросами кода от одного креатора

client: "LolzClient | None" = None
_last_code_request: dict[int, float] = {}


class LolzError(Exception):
    def __init__(self, status: int, errors: list[Any]):
        self.status = status
        self.errors = [str(e) for e in errors]
        super().__init__(f"LOLZ {status}: {'; '.join(self.errors)}")

    @property
    def retry(self) -> bool:
        return any("retry_request" in e for e in self.errors)

    @property
    def no_money(self) -> bool:
        text = " ".join(self.errors).lower()
        return any(word in text for word in ("balance", "баланс", "средств"))


def _seller_rating(item: dict) -> int:
    """Рейтинг продавца: положительные отзывы минус отрицательные."""
    raw = item.get("feedback_data") or "{}"
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return 0
    if not isinstance(data, dict):
        return 0
    return sum(int(f.get("positive") or 0) - int(f.get("negative") or 0) for f in data.values() if isinstance(f, dict))


def _price(item: dict) -> float:
    return float(item.get("rub_price") or item.get("price") or 0)


def phone_of(item: dict) -> str | None:
    """Номер аккаунта. loginData.login у LOLZ часто не номер, а ключ сессии, его не показываем."""
    for value in (item.get("telegram_phone"), (item.get("loginData") or {}).get("login")):
        digits = str(value or "").strip().lstrip("+")
        if digits.isdigit() and 7 <= len(digits) <= 15:
            return f"+{digits}"
    return None


class LolzClient:
    def __init__(self, token: str, base_url: str = "https://api.lzt.market"):
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=httpx.Timeout(60, connect=15),
        )

    async def close(self):
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        for attempt in range(6):
            try:
                resp = await self._http.request(method, path, **kwargs)
            except httpx.HTTPError as e:
                if attempt == 5:
                    raise LolzError(0, [f"network: {e!r}"]) from e
                await asyncio.sleep(2)
                continue
            if resp.status_code == 429:
                reset = resp.headers.get("X-RateLimit-Reset")
                wait = float(reset) - time.time() if reset and reset.isdigit() else 5
                await asyncio.sleep(min(max(wait, 1), 30))
                continue
            try:
                data = resp.json()
            except ValueError:
                data = {"errors": [resp.text[:300]]}
            if resp.status_code >= 400 or data.get("errors"):
                err = LolzError(resp.status_code, data.get("errors") or [resp.text[:300]])
                if err.retry and attempt < 5:  # LOLZ просит повторить тот же запрос
                    await asyncio.sleep(1)
                    continue
                raise err
            return data
        raise LolzError(429, ["rate limit"])

    async def search_telegram(self, country: str, max_price: float, min_rating: int, want: int = 20) -> list[dict]:
        """Самые дешёвые авторег-аккаунты страны: без спамблока, пароля и премиума, не продавались ранее,
        без чужих сессий, от продавцов с нужным рейтингом. Идём вверх по цене, пока не наберём want лотов."""
        found: dict[int, dict] = {}
        pmin: float | None = None
        for _ in range(SEARCH_PAGES):
            params = [
                ("country[]", country), ("origin[]", "autoreg"), ("spam", "no"), ("password", "no"),
                ("premium", "no"), ("nsb", "1"), ("max_authorizations", "1"), ("pmax", f"{max_price:.2f}"),
                ("currency", "rub"), ("order_by", "price_to_up"),
            ]
            if pmin is not None:
                params.append(("pmin", f"{pmin:.2f}"))
            items = (await self._request("GET", "/telegram", params=params)).get("items") or []
            for item in items:
                if not (item.get("seller") or {}).get("is_banned") and _seller_rating(item) >= min_rating:
                    found.setdefault(item["item_id"], item)
            if len(found) >= want or len(items) < PAGE_SIZE:
                break
            prices = [_price(i) for i in items]
            # Вся страница одной цены: перескакиваем её (LOLZ округляет pmin до рубля)
            pmin = math.floor(max(prices)) + 1 if min(prices) == max(prices) else max(prices)
            if pmin > max_price:
                break
        return sorted(found.values(), key=_price)

    async def item(self, item_id: int) -> dict:
        data = await self._request("GET", f"/{item_id}")
        return data.get("item", data)

    async def fast_buy(self, item_id: int) -> dict:
        """Проверка аккаунта и покупка одним запросом."""
        return (await self._request("POST", f"/{item_id}/fast-buy"))["item"]

    async def login_code(self, item_id: int) -> str | None:
        data = await self._request("GET", f"/{item_id}/telegram-login-code")
        codes = data.get("codes")
        if isinstance(codes, list):
            codes = codes[0] if codes else None
        return str(codes["code"]) if codes and codes.get("code") else None


@dataclass
class BuyResult:
    item: dict | None = None
    no_money: bool = False
    error: str | None = None

    @property
    def phone(self) -> str:
        return phone_of(self.item) or "—"

    @property
    def cost(self) -> float:
        return _price(self.item)


async def buy_account(country: str, max_price: float, min_rating: int) -> BuyResult:
    """Покупает один самый дешёвый подходящий аккаунт страны."""
    tried: set[int] = set()
    for _ in range(MAX_SEARCHES):
        try:
            items = await client.search_telegram(country, max_price, min_rating)
        except LolzError as e:
            log.error("Поиск LOLZ (%s): %s", country, e)
            return BuyResult(error=f"Поиск: {e}")
        fresh = [i for i in items if i["item_id"] not in tried]
        if not fresh:
            break
        for item in fresh:
            tried.add(item["item_id"])
            try:
                result = await client.fast_buy(item["item_id"])
            except LolzError as e:
                if e.no_money:
                    log.error("На LOLZ закончились деньги: %s", e)
                    return BuyResult(no_money=True)
                log.info("Лот %s не куплен: %s", item["item_id"], e)
                continue
            merged = {**item, **result}
            if not phone_of(merged):
                try:  # номер есть в полной карточке лота
                    merged = {**merged, **await client.item(item["item_id"])}
                except LolzError as e:
                    log.warning("Не удалось получить номер лота %s: %s", item["item_id"], e)
            return BuyResult(item=merged)
    return BuyResult()


async def fetch_code(user_id: int, item_id: int) -> tuple[str | None, str | None]:
    """(код, ошибка): ровно одно из двух не None."""
    now = time.monotonic()
    if now - _last_code_request.get(user_id, 0) < CODE_COOLDOWN:
        return None, "Подожди пару секунд и попробуй снова."
    # Старые отметки больше не нужны: чистим, чтобы словарь не рос бесконечно
    for uid in [u for u, t in _last_code_request.items() if now - t > CODE_COOLDOWN]:
        del _last_code_request[uid]
    _last_code_request[user_id] = now
    try:
        code = await client.login_code(item_id)
    except LolzError as e:
        if e.status != 404:
            log.error("Код для лота %s: %s", item_id, e)
            return None, "Не удалось получить код, попробуй через минуту."
        code = None
    if code:
        return code, None
    return None, "Код пока не пришёл. Введи номер в Telegram, дождись отправки кода и запроси снова через 10–20 секунд."
