"""実機で「毎秒何枚撮って流せるか」を測る。

  1) カメラ単体: cap.read() だけを回して、届くコマ数/秒
  2) 1 枚あたりの処理: 縮小＋グレー化／動きの判定／JPEG 化 の時間と大きさ
  3) 通し: 撮影スレッドと圧縮スレッドを本番と同じ形で回し、圧縮まで流せた枚数/秒と取りこぼし

送信はしない（回線の速さは別の話なので混ぜない）。
"""

from __future__ import annotations

import queue
import statistics
import threading
import time

import cv2

from .app import encode_jpeg
from .camera import fit, make_camera
from .config import Config
from .motion import MotionJudge


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]


def bench(cfg: Config, seconds: float = 20.0):
    cam = make_camera(cfg.camera)
    cam.open()
    print(f"camera: {cfg.camera.kind} {cfg.camera.device} 要求 {cfg.camera.width}x{cfg.camera.height} "
          f"{cfg.camera.fps}fps {cfg.camera.fourcc} → 長辺 {cfg.camera.long_px} に縮める")
    if hasattr(cam, "cap") and cam.cap is not None:
        c = cam.cap
        print(f"  実際: {int(c.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(c.get(cv2.CAP_PROP_FRAME_HEIGHT))} "
              f"{c.get(cv2.CAP_PROP_FPS):.1f}fps")

    # 1) カメラ単体
    if hasattr(cam, "cap") and cam.cap is not None:
        n, t0 = 0, time.monotonic()
        while time.monotonic() - t0 < min(seconds, 10):
            ok, _ = cam.cap.read()
            n += ok
        el = time.monotonic() - t0
        print(f"[1] カメラ単体: {n / el:.1f} コマ/秒（{n} コマ / {el:.1f} 秒）")

    # 2) 1 枚あたり
    judge = MotionJudge()
    t_sq, t_mo, t_jp, sizes = [], [], [], []
    for _ in range(60):
        f = cam.read()
        if f is None:
            continue
        t = time.perf_counter()
        bgr = fit(f.bgr, cfg.camera.long_px)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        t_sq.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        judge.judge(gray)
        t_mo.append((time.perf_counter() - t) * 1000)
        t = time.perf_counter()
        jpg = encode_jpeg(bgr, cfg.capture.jpeg_quality)
        t_jp.append((time.perf_counter() - t) * 1000)
        sizes.append(len(jpg) / 1024)
        shape = bgr.shape
    if t_jp:
        print(f"    送る絵の大きさ: {shape[1]}x{shape[0]}")
        print(f"[2] 1 枚あたり（ms・中央値/95%）: 縮小+グレー {statistics.median(t_sq):.2f}/{_pct(t_sq, .95):.2f}"
              f"  動き判定 {statistics.median(t_mo):.2f}/{_pct(t_mo, .95):.2f}"
              f"  JPEG(q={cfg.capture.jpeg_quality}) {statistics.median(t_jp):.2f}/{_pct(t_jp, .95):.2f}"
              f"  大きさ {statistics.median(sizes):.0f}KB")

    # 3) 通し（本番と同じ 2 スレッド）
    q: queue.Queue = queue.Queue(maxsize=cfg.capture.queue_max)
    stop = threading.Event()
    counts = {"got": 0, "full": 0, "encoded": 0, "bytes": 0}

    def encoder():
        while not stop.is_set() or not q.empty():
            try:
                f = q.get(timeout=0.2)
            except queue.Empty:
                continue
            jpg = encode_jpeg(f.bgr, cfg.capture.jpeg_quality)
            counts["encoded"] += 1
            counts["bytes"] += len(jpg)

    th = threading.Thread(target=encoder, daemon=True)
    th.start()
    judge.reset()
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        f = cam.read()
        if f is None:
            continue
        counts["got"] += 1
        judge.judge(f.gray)
        try:
            q.put_nowait(f)
        except queue.Full:
            counts["full"] += 1
    el = time.monotonic() - t0
    stop.set()
    th.join(timeout=10)
    cam.close()
    got = counts["got"]
    print(f"[3] 通し {el:.0f} 秒: 撮れた {got / el:.1f} 枚/秒・圧縮できた {counts['encoded'] / el:.1f} 枚/秒・"
          f"圧縮待ち満杯 {counts['full']} 回（{100 * counts['full'] / max(got, 1):.1f}%・本番ではディスクへ逃がす）・"
          f"全部送ると {counts['bytes'] / el / 1024 / 1024 * 3600 / 1024:.2f} GB/時")
    temp = None
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as fh:
            temp = int(fh.read()) / 1000
    except OSError:
        pass
    if temp is not None:
        print(f"    CPU 温度 {temp:.1f}℃")
