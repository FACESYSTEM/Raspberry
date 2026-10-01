"""カメラ。USB UVC カメラ（OpenCV の V4L2 経由）と、試験用の作り物。

出すのは「画面全体を残したまま、長い辺を long_px に縮めた BGR 画像」と、そのグレー版。
Android 版の frameSmallNv21（正立・長辺 640 以内・縦横比はそのまま・切り抜かない）に合わせる。
1280×720 なら 640×360。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

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


def make_camera(cfg: CameraConfig):
    if cfg.kind == "opencv":
        return OpenCvCamera(cfg)
    if cfg.kind == "fake":
        return FakeCamera(cfg)
    raise ValueError(f"camera.kind が不明: {cfg.kind}")
