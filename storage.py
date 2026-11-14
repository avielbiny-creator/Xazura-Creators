"""Черновики заявок в базе, а не в памяти: перезапуск бота не сбрасывает начатую заявку."""
import json
from collections.abc import Mapping
from typing import Any

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

import db


def _key(key: StorageKey) -> str:
    return ":".join(str(part or "") for part in (
        key.bot_id, key.business_connection_id, key.chat_id, key.user_id, key.thread_id, key.destiny,
    ))


class DBStorage(BaseStorage):
    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await db.fsm_set(_key(key), value, await self.get_data(key))

    async def get_state(self, key: StorageKey) -> str | None:
        row = await db.fsm_get(_key(key))
        return row["state"] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:
        await db.fsm_set(_key(key), await self.get_state(key), dict(data))

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        row = await db.fsm_get(_key(key))
        return json.loads(row["data"]) if row else {}

    async def close(self) -> None:
        pass
