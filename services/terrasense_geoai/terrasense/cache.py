from __future__ import annotations

import threading
import time
from typing import Any


class TTLCache:
    def __init__(self, ttl_s: int = 600, max_items: int = 256) -> None:
        self.ttl_s = ttl_s
        self.max_items = max_items
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        now = time.time()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires, value = item
            if expires < now:
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._data) >= self.max_items:
                oldest = min(self._data.items(), key=lambda kv: kv[1][0])[0]
                self._data.pop(oldest, None)
            self._data[key] = (time.time() + self.ttl_s, value)


http_cache = TTLCache()
