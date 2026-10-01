"""無人コマの判定（specs.md「生コマの無人判定を『差分』でやる」R-164 と同じ値）。

- 画像を 16×16 = 256 セルに割り、セルごとの平均輝度を直前のコマと比べる
- 最大セル差が T=8 以上なら「動き」
- 動きの前後 5 秒（保護帯）に入るコマは残す。外は無人＝送らない
- 判定できないコマは全部「動き」扱い＝送る側に倒す

値はサーバ版（tools/diff_unmanned.py）・Android 版（s138）と同じ固定値。変えない。
"""

from __future__ import annotations

import bisect
import threading
from collections import deque

import cv2
import numpy as np

GRID = 16
THRESHOLD = 8
GUARD_MS = 5_000
# 動きの時刻を持っておく長さ。ディスクへ逃がしたコマ（最大で数分古い）も判定できるように
RETENTION_MS = 15 * 60_000


def cell_means(gray: np.ndarray) -> np.ndarray:
    """グレー画像 → 16×16 のセル平均。

    INTER_AREA で 16×16 に縮めると、各画素はちょうど対応するセルの平均になる
    （640 なら 40×40 画素ずつ）。
    """
    return cv2.resize(gray, (GRID, GRID), interpolation=cv2.INTER_AREA).astype(np.int16)


class MotionJudge:
    """カメラ順に 1 コマずつ渡し、「動き」かを返す。撮影スレッドだけが呼ぶ。"""

    def __init__(self):
        self._prev: np.ndarray | None = None

    def reset(self):
        """カメラを開き直したときなど、直前のコマとの比較が意味を失ったとき。"""
        self._prev = None

    def judge(self, gray: np.ndarray) -> bool:
        try:
            cells = cell_means(gray)
        except Exception:
            self._prev = None
            return True
        prev, self._prev = self._prev, cells
        if prev is None or prev.shape != cells.shape:
            return True  # 先頭は判定できない＝動き扱い
        return int(np.abs(cells - prev).max()) >= THRESHOLD


class MotionLog:
    """動きの時刻と、どこまで判定が済んだかを持つ。保護帯の判定をする。

    撮影スレッドが record() し、束ねる側が decide() する。
    """

    KEEP = "keep"
    DROP = "drop"
    PENDING = "pending"

    def __init__(self, guard_ms: int = GUARD_MS, retention_ms: int = RETENTION_MS):
        self.guard_ms = guard_ms
        self.retention_ms = retention_ms
        self._lock = threading.Lock()
        self._motion: deque[int] = deque()
        self._since: int | None = None  # 判定を持っている最古の時刻
        self._latest: int | None = None  # 判定済みの最新のコマ

    def record(self, t_ms: int, moving: bool):
        with self._lock:
            if self._since is None:
                self._since = t_ms
            if self._latest is not None and t_ms < self._latest:
                # 時計が戻った。前後関係が信用できないので、ここから数え直す
                self._motion.clear()
                self._since = t_ms
            self._latest = t_ms
            if moving:
                self._motion.append(t_ms)
            cutoff = t_ms - self.retention_ms
            if self._since < cutoff:
                self._since = cutoff
                while self._motion and self._motion[0] < cutoff - self.guard_ms:
                    self._motion.popleft()

    def latest(self) -> int | None:
        with self._lock:
            return self._latest

    def decide(self, t_ms: int, force: bool = False) -> str:
        """そのコマを送るか。

        保護帯は「5 秒後まで」を見るので、そのコマの 5 秒後まで判定が済むまでは PENDING。
        force=True（止めるとき）は待たずに、決まらないものは送る側に倒す。
        """
        with self._lock:
            if self._since is None or t_ms < self._since:
                return self.KEEP  # 判定を持っていない＝判定不能＝送る
            if self._latest is None or self._latest < t_ms + self.guard_ms:
                if not force:
                    return self.PENDING
                return self.KEEP
            lo = bisect.bisect_left(self._motion, t_ms - self.guard_ms)
            if lo < len(self._motion) and self._motion[lo] <= t_ms + self.guard_ms:
                return self.KEEP
            return self.DROP
