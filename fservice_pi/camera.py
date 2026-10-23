"""カメラ。USB UVC カメラ（OpenCV の V4L2 経由）、個室の小型カメラ（受け口経由）、試験用の作り物。

出すのは「画面全体を残したまま、長い辺を long_px に縮めた BGR 画像」と、そのグレー版。
Android 版の frameSmallNv21（正立・長辺 640 以内・縦横比はそのまま・切り抜かない）に合わせる。
1280×720 なら 640×360。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import CameraConfig

log = logging.getLogger(__name__)


@dataclass
class Frame:
    t_ms: int  # 取得した時刻（UNIX ミリ秒）。サーバのエントリ名になる
    bgr: np.ndarray
    gray: np.ndarray


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def fit(bgr: np.ndarray, long_px: int) -> np.ndarray:
    """長い辺を long_px 以内に縮める（縦横比はそのまま・切り抜かない）。縦横は偶数にそろえる。"""
    h, w = bgr.shape[:2]
    k = min(1.0, long_px / max(h, w))
    nw, nh = int(w * k), int(h * k)
    nw -= nw % 2
    nh -= nh % 2
    if (nw, nh) == (w, h):
        return bgr
    return cv2.resize(bgr, (nw, nh), interpolation=cv2.INTER_AREA)


class OpenCvCamera:
    def __init__(self, cfg: CameraConfig):
        self.cfg = cfg
        self.cap: cv2.VideoCapture | None = None

    def open(self):
        self.close()
        dev = self.cfg.device
        cap = cv2.VideoCapture(int(dev) if dev.isdigit() else dev, cv2.CAP_V4L2)
        if not cap.isOpened():
            raise RuntimeError(f"カメラを開けない: {dev}")
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*self.cfg.fourcc))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.cfg.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.cfg.height)
        cap.set(cv2.CAP_PROP_FPS, self.cfg.fps)
        # 古いコマを溜めない（遅れて届くと時刻がずれる）
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        log.info("カメラを開いた %s %dx%d %.1ffps（要求 %dx%d %dfps %s）",
                 dev, w, h, fps, self.cfg.width, self.cfg.height, self.cfg.fps, self.cfg.fourcc)
        self.cap = cap

    def read(self) -> Frame | None:
        if self.cap is None:
            return None
        ok, img = self.cap.read()
        if not ok or img is None:
            return None
        t = now_ms()
        bgr = fit(img, self.cfg.long_px)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        return Frame(t, bgr, gray)

    def close(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None


class FakeCamera:
    """試験用。決まった fps で、ときどき四角が動く絵を出す。"""

    def __init__(self, cfg: CameraConfig, moving_every_s: float = 20.0, moving_for_s: float = 3.0):
        self.cfg = cfg
        self.moving_every_s = moving_every_s
        self.moving_for_s = moving_for_s
        self._next = 0.0
        self._t0 = time.monotonic()

    def open(self):
        self._next = time.monotonic()

    def read(self) -> Frame | None:
        delay = self._next - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        self._next = max(self._next + 1.0 / self.cfg.fps, time.monotonic() - 1.0)
        n = self.cfg.long_px
        bgr = np.full((n * 9 // 16, n, 3), 90, np.uint8)
        el = time.monotonic() - self._t0
        if el % self.moving_every_s < self.moving_for_s:
            x = int((el * 200) % (n - 100))
            bgr[n // 4:n // 4 + 60, x:x + 100] = 230
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        return Frame(now_ms(), bgr, gray)

    def close(self):
        pass


class InboxCamera:
    """個室の小型カメラ（ESP32）用。受け口（hub）が受け取った写真を、device に書いたフォルダから
    撮った時刻の順に読む。

    写真は人感センサーが反応したときだけ届くので、来ない時間が長くても止まったと見なさない（idle_ok）。
    撮影窓はカメラ側が撮り始めるときに守っているので、届いた分はそのまま流す（pushed）。
    """

    idle_ok = True
    pushed = True
    STILL_MAX_AGE_S = 600  # 生存確認に使う静止画は、これより古ければ使わない

    def __init__(self, cfg: CameraConfig):
        self.cfg = cfg
        self.dir = Path(cfg.device)
        self.still_path = self.dir.parent / "still.jpg"
        self.status_path = self.dir.parent / "status.json"

    def open(self):
        self.dir.mkdir(parents=True, exist_ok=True)

    def _oldest(self) -> Path | None:
        try:
            names = [e.name for e in os.scandir(self.dir) if e.name.endswith(".jpg")]
        except OSError:
            return None
        return self.dir / min(names) if names else None

    def read(self) -> Frame | None:
        while True:
            path = self._oldest()
            if path is None:
                time.sleep(0.2)
                return None
            try:
                t = int(path.stem)
                data = path.read_bytes()
            except (ValueError, OSError):
                t, data = 0, b""
            try:
                path.unlink()
            except OSError:
                pass
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if t > 0 and data else None
            if img is None:
                log.warning("読めない写真を捨てた: %s", path.name)
                continue
            bgr = fit(img, self.cfg.long_px)
            return Frame(t, bgr, cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))

    def still_jpeg(self) -> bytes | None:
        """カメラが 5 分ごとに送ってくる静止画（生存確認・画角確認用）。古ければ None。"""
        try:
            if time.time() - self.still_path.stat().st_mtime > self.STILL_MAX_AGE_S:
                return None
            return self.still_path.read_bytes()
        except OSError:
            return None

    def status(self) -> dict:
        """カメラが静止画と一緒に送ってくる状態（電波・温度・再起動の回数など）。無ければ空。"""
        try:
            data = json.loads(self.status_path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def close(self):
        pass


def make_camera(cfg: CameraConfig):
    if cfg.kind == "opencv":
        return OpenCvCamera(cfg)
    if cfg.kind == "fake":
        return FakeCamera(cfg)
    if cfg.kind == "inbox":
        return InboxCamera(cfg)
    raise ValueError(f"camera.kind が不明: {cfg.kind}")
