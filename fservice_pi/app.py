"""本体。スレッドの組み立て。

  撮影スレッド  カメラ → 正方形 640 → 動きの判定 → 圧縮待ちへ（満杯ならディスクへ逃がす）
  圧縮スレッド  圧縮待ち（空ならディスクの残り）→ JPEG → 束ね
  送信スレッド  送信待ち（ディスク）→ POST /v1/detframes
  心拍スレッド  GET /v1/config（face_params を受け取る）
  見張り        束ねの締め・申告・systemd のウォッチドッグ
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import queue
import socket
import threading
import time

import cv2

from . import VERSION
from .api import ServerApi
from .camera import Frame, make_camera, now_ms
from .config import Config
from .motion import MotionJudge, MotionLog
from .spill import Spill, free_mb
from .stats import Counters
from .uploader import ENTRY_MAX, Batcher, Outbox, Sender

log = logging.getLogger(__name__)


def encode_jpeg(bgr, quality: int) -> bytes:
    """1 枚 200KB を超えたら画質を下げて作り直す（サーバの上限）。"""
    q = quality
    while True:
        ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, q])
        if not ok:
            raise RuntimeError("JPEG にできない")
        if len(buf) <= ENTRY_MAX or q <= 20:
            return buf.tobytes()
        q -= 15


def parse_windows(windows: list[str]) -> list[tuple[int, int]]:
    out = []
    for w in windows:
        a, b = w.split("-")
        ha, ma = (int(x) for x in a.split(":"))
        hb, mb = (int(x) for x in b.split(":"))
        out.append((ha * 60 + ma, hb * 60 + mb))
    return out


def in_windows(windows: list[tuple[int, int]], minute_of_day: int) -> bool:
    if not windows:
        return True
    for start, end in windows:
        if start <= end:
            if start <= minute_of_day < end:
                return True
        elif minute_of_day >= start or minute_of_day < end:  # 日またぎ
            return True
    return False


def sd_notify(msg: str):
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    if addr.startswith("@"):
        addr = "\0" + addr[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(addr)
            s.sendall(msg.encode())
    except OSError:
        pass


def cpu_temp_c() -> float | None:
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read().strip()) / 1000.0
    except (OSError, ValueError):
        return None


class App:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.c = Counters()
        self.api = ServerApi(cfg.server)
        self.camera = make_camera(cfg.camera)
        self.judge = MotionJudge()
        self.motion = MotionLog()
        self.spill = Spill(cfg.spill_dir, cfg.camera.out_size, cfg.storage.min_free_mb)
        self.outbox = Outbox(cfg.outbox_dir, cfg.failed_dir, cfg.storage.min_free_mb, self.c)
        self.batcher = Batcher(self.motion, self.outbox, self.c, self.mode)
        self.sender = Sender(self.api, self.outbox, self.c)
        self.windows = parse_windows(cfg.capture.windows)
        self.face_params: dict = {}
        self._q: queue.Queue[Frame] = queue.Queue(maxsize=cfg.capture.queue_max)
        self._stop = threading.Event()
        self._capture_done = threading.Event()
        self._started = time.monotonic()
        self._capturing = False
        self._last_capture_loop = time.monotonic()
        self._fps_times: list[float] = []
        self._threads: list[threading.Thread] = []

    # --- 設定 ---

    def mode(self) -> int:
        v = self.face_params.get("raw_diff_filter", self.cfg.capture.raw_diff_filter)
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 0  # 読めない値は「全部送る」側に倒す
        return v if v in (0, 1, 2) else 0

    def cam_fps(self) -> float:
        """直近 10 秒の取得コマ数から。撮影窓の外は -1（Android 版と同じ）。"""
        if not self._capturing:
            return -1.0
        now = time.monotonic()
        recent = [t for t in self._fps_times if now - t <= 10.0]
        return round(len(recent) / 10.0, 1)

    # --- 起動・停止 ---

    def start(self):
        self.spill.start()
        self.sender.start()
        for name, target in (("capture", self._capture_loop), ("encoder", self._encoder_loop),
                             ("heartbeat", self._heartbeat_loop), ("ticker", self._ticker_loop)):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        sd_notify("READY=1")
        log.info("起動 %s mode=%d windows=%s", VERSION, self.mode(), self.cfg.capture.windows or "常時")

    def request_stop(self):
        self._stop.set()

    def shutdown(self):
        log.info("停止します")
        sd_notify("STOPPING=1")
        self._stop.set()
        self._capture_done.wait(timeout=10)
        for t in self._threads:
            t.join(timeout=15)
        self.batcher.flush(force=True)
        self.spill.stop()
        self.sender.stop()
        self.camera.close()
        # 止める時点の数を残す（送れなかった分は送信待ち・退避に残っていて、次の起動で送る）
        log.info("停止時の申告 %s", self.stat_line()["det_frames"])

    def run_forever(self):
        self.start()
        try:
            while not self._stop.is_set():
                self._stop.wait(1)
        except KeyboardInterrupt:
            pass
        finally:
            self.shutdown()

    # --- 撮影 ---

    def _capture_loop(self):
        opened = False
        last_frame = time.monotonic()
        open_backoff = 1.0
        try:
            while not self._stop.is_set():
                self._last_capture_loop = time.monotonic()
                now_local = dt.datetime.now()
                if not in_windows(self.windows, now_local.hour * 60 + now_local.minute):
                    if opened:
                        log.info("撮影窓の外。カメラを止める")
                        self.camera.close()
                        opened = False
                        self._capturing = False
                        self.batcher.flush(force=True)
                    self._stop.wait(5)
                    continue
                if not opened:
                    try:
                        self.camera.open()
                    except Exception as e:
                        self.c.add("cam_open_err")
                        log.error("カメラを開けない（%.0f 秒後にやり直す）: %s", open_backoff, e)
                        self._stop.wait(open_backoff)
                        open_backoff = min(open_backoff * 2, 30.0)
                        continue
                    opened, open_backoff = True, 1.0
                    self._capturing = True
                    self.judge.reset()
                    last_frame = time.monotonic()
                frame = self.camera.read()
                if frame is None:
                    if time.monotonic() - last_frame > self.cfg.camera.stall_s:
                        self.c.add("cam_stall")
                        log.error("%.0f 秒コマが来ない。カメラを開き直す", self.cfg.camera.stall_s)
                        self.camera.close()
                        opened = False
                    else:
                        time.sleep(0.01)
                    continue
                last_frame = time.monotonic()
                self._fps_times.append(last_frame)
                if len(self._fps_times) > 1000:
                    self._fps_times = self._fps_times[-600:]
                self.c.add("raw_frames")
                self.motion.record(frame.t_ms, self.judge.judge(frame.gray))
                try:
                    self._q.put_nowait(frame)
                except queue.Full:
                    if self.spill.offer(frame):
                        self.c.add("spilled")
                    else:
                        self.c.add("raw_dropped")
        finally:
            self.camera.close()
            self._capturing = False
            self._capture_done.set()

    # --- 圧縮 ---

    def _encoder_loop(self):
        q = self.cfg.capture.jpeg_quality
        while True:
            try:
                frame = self._q.get(timeout=0.2)
            except queue.Empty:
                if self._stop.is_set() and self._capture_done.is_set():
                    return  # 圧縮待ちを空にしてから終わる。ディスクの残りは次の起動で拾う
                got = self.spill.take()
                if got is None:
                    continue
                t_ms, bgr, path = got
                try:
                    self.batcher.add(t_ms, encode_jpeg(bgr, q))
                    self.spill.done(path)
                except Exception as e:
                    self.c.add("encode_err")
                    log.error("退避分を JPEG にできない: %s", e)
                    self.spill.back(path)
                    time.sleep(1)
                continue
            try:
                self.batcher.add(frame.t_ms, encode_jpeg(frame.bgr, q))
            except Exception as e:
                self.c.add("encode_err")
                log.error("JPEG にできない: %s", e)

    # --- 心拍 ---

    def telemetry(self) -> dict:
        snap = self.c.snapshot()
        out = {
            "app_ver": VERSION,
            "cam_fps": self.cam_fps(),
            "uptime_s": int(time.monotonic() - self._started),
            "free_mb": free_mb(self.outbox.root),
            "diff_filter": self.mode(),
            "outbox_frames": self.outbox.stats()["outbox_frames"],
            "raw_sent": snap.get("raw_sent", 0),
        }
        temp = cpu_temp_c()
        if temp is not None:
            out["temp_c"] = temp
        return out

    def _heartbeat_loop(self):
        while not self._stop.is_set():
            if self.api.enabled:
                res = self.api.heartbeat(self.telemetry())
                if isinstance(res, dict) and isinstance(res.get("face_params"), dict):
                    before = self.mode()
                    self.face_params = res["face_params"]
                    if self.mode() != before:
                        log.info("raw_diff_filter が %d → %d", before, self.mode())
            self._stop.wait(self.cfg.server.heartbeat_s)

    # --- 見張り ---

    def stat_line(self) -> dict:
        det = self.c.snapshot()
        det.update(self.spill.stats())
        det.update(self.outbox.stats())
        det["queued"] = self._q.qsize()
        det["pending"] = self.batcher.pending_count()
        det["diff_filter"] = self.mode()
        det["cam_fps"] = self.cam_fps()
        return {"t": now_ms(), "kind": "stat", "det_frames": det}

    def _ticker_loop(self):
        next_stat = time.monotonic() + self.cfg.server.stat_s
        while not self._stop.is_set():
            self._stop.wait(1)
            self.batcher.flush()
            # 撮影スレッドが回っているときだけウォッチドッグを撫でる（固まったら systemd が落とす）
            if time.monotonic() - self._last_capture_loop < 30:
                sd_notify("WATCHDOG=1")
            if time.monotonic() >= next_stat:
                next_stat = time.monotonic() + self.cfg.server.stat_s
                line = self.stat_line()
                log.info("申告 %s", line["det_frames"])
                if self.api.enabled and self.cfg.server.send_stat:
                    result, detail = self.api.post_detlog([line])
                    if result.value != "ok":
                        log.warning("申告を送れない: %s", detail)
