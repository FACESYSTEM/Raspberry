"""JPEG を束ねて ZIP にし、ディスクの送信待ち（outbox）へ置き、古い順に送る。

- サーバの検査に合わせる: エントリ名は `<t>r.jpg`（生コマ）、各 200KB 以下、合計 8MB 以下、無圧縮
- 束は 60 枚か 10 秒（壁時計）で切る
- 保護帯の判定はそのコマの 5 秒後まで見えてから確定する（それまでは束に入れずに持つ）
- 送れなければ outbox に残して送り直す。回線が切れていても撮影は止めない
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .api import Sent, ServerApi
from .motion import MotionLog
from .spill import free_mb
from .stats import Counters

log = logging.getLogger(__name__)

ENTRY_MAX = 200 * 1024
ZIP_MAX = 8 * 1024 * 1024
# ZIP の見出しぶんの余裕を見て、束の中身はここまでにする
BATCH_BYTES = ZIP_MAX - 256 * 1024
BATCH_FRAMES = 60
BATCH_SECONDS = 10.0


@dataclass
class Item:
    t_ms: int
    jpeg: bytes


def build_zip(items: list[Item]) -> bytes:
    buf = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as z:
        for it in items:
            t = it.t_ms
            while f"{t}r.jpg" in used:
                t += 1  # 同じミリ秒の 2 枚目（実際にはまず起きない）
            name = f"{t}r.jpg"
            used.add(name)
            z.writestr(name, it.jpeg)
    return buf.getvalue()


class Outbox:
    """送信待ちの ZIP。ファイル名は `<最初のコマの時刻>-<枚数>.zip`。"""

    def __init__(self, root: Path, failed: Path, min_free_mb: int, counters: Counters):
        self.root = root
        self.failed = failed
        self.min_free_mb = min_free_mb
        self.c = counters
        root.mkdir(parents=True, exist_ok=True)
        failed.mkdir(parents=True, exist_ok=True)
        for p in root.glob("*.tmp"):
            p.unlink(missing_ok=True)

    def put(self, items: list[Item]) -> bool:
        if not items:
            return True
        fm = free_mb(self.root)
        if fm < 0 or fm < self.min_free_mb:
            self.c.add("outbox_full_dropped", len(items))
            log.error("空きが %d MB しかないので %d 枚を捨てた", fm, len(items))
            return False
        body = build_zip(items)
        name = f"{items[0].t_ms}-{len(items)}.zip"
        tmp = self.root / (name + ".tmp")
        try:
            tmp.write_bytes(body)
            os.replace(tmp, self.root / name)
        except OSError as e:
            tmp.unlink(missing_ok=True)
            self.c.add("outbox_err")
            self.c.add("outbox_err_dropped", len(items))
            log.error("送信待ちに書けない（%d 枚を捨てた）: %s", len(items), e)
            return False
        self.c.add("batched", len(items))
        return True

    def oldest(self) -> Path | None:
        files = sorted(self.root.glob("*.zip"), key=lambda p: int(p.name.split("-")[0]))
        return files[0] if files else None

    def stats(self) -> dict:
        n = kb = frames = 0
        for p in self.root.glob("*.zip"):
            n += 1
            try:
                kb += p.stat().st_size // 1024
                frames += int(p.stem.split("-")[1])
            except (OSError, ValueError, IndexError):
                pass
        return {"outbox_files": n, "outbox_frames": frames, "outbox_kb": kb}

    def reject(self, path: Path, reason: str):
        try:
            os.replace(path, self.failed / path.name)
            (self.failed / (path.name + ".reason.txt")).write_text(reason)
        except OSError as e:
            log.error("拒否された束を退避できない: %s", e)


class Batcher:
    """JPEG を受け取り、保護帯の判定が確定したものから束ねる。"""

    def __init__(self, motion: MotionLog, outbox: Outbox, counters: Counters,
                 mode: Callable[[], int], clock: Callable[[], float] = time.monotonic):
        self.motion = motion
        self.outbox = outbox
        self.c = counters
        self.mode = mode
        self.clock = clock
        self._lock = threading.Lock()
        self._pending: list[Item] = []
        self._batch: list[Item] = []
        self._batch_bytes = 0
        self._batch_started: float | None = None

    def add(self, t_ms: int, jpeg: bytes):
        with self._lock:
            self._pending.append(Item(t_ms, jpeg))
        self.flush()

    def flush(self, force: bool = False):
        mode = self.mode()
        with self._lock:
            still: list[Item] = []
            for it in self._pending:
                if mode == 0:
                    self._to_batch(it)
                    continue
                d = self.motion.decide(it.t_ms, force=force)
                if d == MotionLog.PENDING:
                    still.append(it)
                elif d == MotionLog.KEEP:
                    self._to_batch(it)
                elif mode == 1:  # 影: 落とすはずだったものを数えて、送る
                    self.c.add("diff_would_drop")
                    self._to_batch(it)
                else:
                    self.c.add("diff_dropped")
            self._pending = still
            due = self._batch_started is not None and (
                self.clock() - self._batch_started >= BATCH_SECONDS)
            if self._batch and (force or due):
                self._cut()

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending) + len(self._batch)

    # 以下、_lock を持った状態で呼ぶ

    def _to_batch(self, it: Item):
        if len(it.jpeg) > ENTRY_MAX:
            self.c.add("too_big_dropped")
            log.warning("1 枚が %d KB で上限を超えた（捨てた）", len(it.jpeg) // 1024)
            return
        if self._batch and self._batch_bytes + len(it.jpeg) > BATCH_BYTES:
            self._cut()
        if not self._batch:
            self._batch_started = self.clock()
        self._batch.append(it)
        self._batch_bytes += len(it.jpeg)
        if len(self._batch) >= BATCH_FRAMES:
            self._cut()

    def _cut(self):
        batch = sorted(self._batch, key=lambda x: x.t_ms)
        self._batch, self._batch_bytes, self._batch_started = [], 0, None
        self.outbox.put(batch)


class Sender:
    """outbox を古い順に送る。送れたら消す。拒否されたら failed/ へ（消さない）。"""

    def __init__(self, api: ServerApi, outbox: Outbox, counters: Counters):
        self.api = api
        self.outbox = outbox
        self.c = counters
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_ok_ms = 0
        self.last_err = ""
        self.backoff_start = 2.0
        self.backoff_max = 60.0

    def start(self):
        self._thread = threading.Thread(target=self._run, name="sender", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)

    def _run(self):
        backoff = self.backoff_start
        while not self._stop.is_set():
            if not self.api.ready:
                self._stop.wait(5)
                continue
            path = self.outbox.oldest()
            if path is None:
                self._stop.wait(1)
                continue
            try:
                body = path.read_bytes()
            except OSError as e:
                self.c.add("outbox_err")
                log.error("送信待ちを読めない %s: %s", path.name, e)
                self.outbox.reject(path, f"read error: {e}")
                continue
            result, detail, reply = self.api.post_detframes(body)
            frames = int(path.stem.split("-")[1])
            if result is Sent.OK:
                path.unlink(missing_ok=True)
                self.c.add("raw_sent", frames)
                # サーバが保存しなかった分（送り直しで既にある＝exists も含む）。数だけ残す
                try:
                    skipped = int(reply.get("skipped") or 0)
                except (TypeError, ValueError):
                    skipped = 0
                if skipped:
                    self.c.add("server_skipped", skipped)
                self.last_ok_ms = time.time_ns() // 1_000_000
                backoff = self.backoff_start
            elif result is Sent.REJECT:
                self.c.add("raw_rejected", frames)
                self.last_err = detail
                log.error("サーバが束を拒否 %s: %s", path.name, detail)
                self.outbox.reject(path, detail)
            else:
                self.c.add("send_retry")
                self.last_err = detail
                log.warning("送れない（%.0f 秒後に送り直す）: %s", backoff, detail)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, self.backoff_max)
