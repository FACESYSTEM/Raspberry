"""圧縮が追いつかない山の間、コマを生のままディスクへ逃がす（R-14「生コマを1枚も捨てない」）。

- 逃がすのは専用スレッド（撮影スレッドを待たせない）
- 取り出すときは .processing へ改名（二重処理を防ぐ）。済んだら消す／失敗したら戻す
- 起動時に .processing を無条件に戻す（落ちたときに抱えていた分を失わない）
- 書けない・読めないは数えて申告に出す。黙って捨てない
"""

from __future__ import annotations

import logging
import os
import queue
import shutil
import threading
from pathlib import Path

import numpy as np

from .camera import Frame

log = logging.getLogger(__name__)

SUFFIX = ".bgr"
PROCESSING = ".processing"


def free_mb(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free // (1024 * 1024)
    except OSError:
        return -1


class Spill:
    def __init__(self, root: Path, min_free_mb: int, writer_queue: int = 64):
        self.root = root
        self.min_free_mb = min_free_mb
        self._q: queue.Queue[Frame | None] = queue.Queue(maxsize=writer_queue)
        self._lock = threading.Lock()
        self._names: list[str] = []
        self.spill_n = 0
        self.spill_err = 0
        self.spill_max_kb = 0
        self._thread: threading.Thread | None = None

    # --- 起動・停止 ---

    def start(self):
        self.root.mkdir(parents=True, exist_ok=True)
        for p in self.root.glob("*" + SUFFIX + PROCESSING):
            try:
                p.rename(p.with_suffix(""))
            except OSError:
                self.spill_err += 1
        for p in self.root.glob("*.tmp"):
            p.unlink(missing_ok=True)  # 書きかけ。中身は信用できない
        self._thread = threading.Thread(target=self._writer, name="spill-writer", daemon=True)
        self._thread.start()

    def stop(self):
        if self._thread is not None:
            self._q.put(None)
            self._thread.join(timeout=10)
            self._thread = None

    # --- 逃がす ---

    def offer(self, frame: Frame) -> bool:
        """受けられれば True。受けられなければ False（呼び手が捨てて数える）。"""
        fm = free_mb(self.root)
        if fm < 0 or fm < self.min_free_mb:
            return False  # 空きが読めないときも受けない（端末を満杯にすると撮影が死ぬ）
        try:
            self._q.put_nowait(frame)
            return True
        except queue.Full:
            return False

    def _writer(self):
        while True:
            frame = self._q.get()
            if frame is None:
                return
            h, w = frame.bgr.shape[:2]
            name = f"{frame.t_ms}_{w}x{h}{SUFFIX}"
            tmp = self.root / (name + ".tmp")
            try:
                frame.bgr.tofile(tmp)
                os.replace(tmp, self.root / name)
                with self._lock:
                    self.spill_n += 1
            except OSError as e:
                with self._lock:
                    self.spill_err += 1
                log.warning("退避に書けない: %s", e)
                tmp.unlink(missing_ok=True)

    # --- 取り出す ---

    def take(self) -> tuple[int, np.ndarray, Path] | None:
        """古い順に 1 枚。読めないものは飛ばす（最大 8 枚まで進む）。"""
        for _ in range(8):
            with self._lock:
                if not self._names:
                    self._names = sorted(
                        (p.name for p in self.root.glob("*" + SUFFIX)),
                        key=lambda s: int(s.split("_")[0].split(".")[0]),
                    )[:512]
                if not self._names:
                    return None
                name = self._names.pop(0)
            src = self.root / name
            dst = self.root / (name + PROCESSING)
            try:
                src.rename(dst)
            except FileNotFoundError:
                continue
            except OSError:
                with self._lock:
                    self.spill_err += 1
                continue
            try:
                t_str, size = name[: -len(SUFFIX)].split("_")
                w, h = (int(x) for x in size.split("x"))
                bgr = np.fromfile(dst, dtype=np.uint8).reshape(h, w, 3)
                return int(t_str), bgr, dst
            except (OSError, ValueError):
                with self._lock:
                    self.spill_err += 1
                self.back(dst)
        return None

    def done(self, path: Path):
        try:
            path.unlink()
        except OSError:
            with self._lock:
                self.spill_err += 1

    def back(self, path: Path):
        try:
            path.rename(path.with_suffix(""))
        except OSError:
            with self._lock:
                self.spill_err += 1

    # --- 申告 ---

    def stats(self) -> dict:
        """実ファイルから数える。"""
        now = kb = stuck = 0
        try:
            for p in self.root.iterdir():
                if p.name.endswith(SUFFIX):
                    now += 1
                    kb += p.stat().st_size // 1024
                elif p.name.endswith(PROCESSING):
                    stuck += 1
        except OSError:
            pass
        with self._lock:
            self.spill_max_kb = max(self.spill_max_kb, kb)
            return {
                "spill_n": self.spill_n,
                "spill_now": now,
                "spill_kb": kb,
                "spill_max_kb": self.spill_max_kb,
                "spill_err": self.spill_err,
                "spill_stuck": stuck,
            }
