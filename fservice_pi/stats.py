"""申告用の数え上げ（スレッドをまたいで足す）。"""

from __future__ import annotations

import threading
from collections import defaultdict


class Counters:
    def __init__(self):
        self._lock = threading.Lock()
        self._c: dict[str, int] = defaultdict(int)

    def add(self, key: str, n: int = 1):
        with self._lock:
            self._c[key] += n

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._c)
