from __future__ import annotations

import time
from typing import Any

_store: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def get(user_id: str, key: str) -> dict[str, Any] | None:
    entry = _store.get((user_id, key))
    if not entry:
        return None

    expires_at, response = entry
    if time.time() > expires_at:
        del _store[(user_id, key)]
        return None

    return response


def set(user_id: str, key: str, response: dict[str, Any], ttl_seconds: int) -> None:
    expires_at = time.time() + ttl_seconds
    _store[(user_id, key)] = (expires_at, response)


def clear(user_id: str, key: str) -> None:
    _store.pop((user_id, key), None)