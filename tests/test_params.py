import time

import numpy as np

from fservice_pi.app import App
from fservice_pi.camera import fit, Frame
from fservice_pi.config import Config
from fservice_pi.motion import MotionLog
from fservice_pi.spill import Spill
from fservice_pi.stats import Counters
from fservice_pi.uploader import Batcher, Outbox


def test_fit_keeps_aspect_like_android():
    assert fit(np.zeros((720, 1280, 3), np.uint8), 640).shape == (360, 640, 3)
    assert fit(np.zeros((1080, 1920, 3), np.uint8), 640).shape == (360, 640, 3)
    assert fit(np.zeros((480, 640, 3), np.uint8), 640).shape == (480, 640, 3)   # 縮めない
    assert fit(np.zeros((1920, 1080, 3), np.uint8), 640).shape == (640, 360, 3)  # 縦長


def test_spill_non_square(tmp_path):
    s = Spill(tmp_path, min_free_mb=0)
    s.start()
    bgr = np.zeros((36, 64, 3), np.uint8)
    bgr[0, 1] = 7
    assert s.offer(Frame(5, bgr, bgr[:, :, 0]))
    s.stop()
    t, got, path = s.take()
    assert t == 5 and got.shape == (36, 64, 3) and got[0, 1, 0] == 7


def test_drop_notes_ranges(tmp_path):
    c = Counters()
    m = MotionLog()
    ob = Outbox(tmp_path / "o", tmp_path / "f", 0, c)
    b = Batcher(m, ob, c, lambda: 2)
    for t in range(0, 20_001, 100):
        m.record(t, t == 0)
        b.add(t, b"x")
    b.flush(force=True)
    notes = b.take_notes((640, 360))
    assert len(notes) == 1
    n = notes[0]
    assert n["kind"] == "note" and n["w"] == 640 and n["f"] == []
    assert n["note"] == "raw_diff drop 100 5100-15000:100"
    assert b.take_notes((640, 360)) == []


def _app(tmp_path, params):
    cfg = Config()
    cfg.camera.kind = "fake"
    cfg.camera.fps = 30
    cfg.camera.long_px = 160
    cfg.capture.raw_diff_filter = 0
    cfg.storage.root = str(tmp_path)
    cfg.storage.min_free_mb = 0
    app = App(cfg)
    app.face_params = params
    return app


def test_raw_fps_throttle_and_off(tmp_path):
    app = _app(tmp_path / "a", {"det_frames_raw_fps": 5})
    app.start(); time.sleep(2.2); app.request_stop(); app.shutdown()
    snap = app.c.snapshot()
    assert snap["raw_frames"] >= 55          # カメラは 30fps のまま回る
    assert 8 <= snap["raw_taken"] <= 13      # 送るのは 5fps ぶん

    app = _app(tmp_path / "b", {"det_frames_raw_fps": 0})
    app.start(); time.sleep(1.2); app.request_stop(); app.shutdown()
    snap = app.c.snapshot()
    assert snap["raw_frames"] > 0 and snap.get("raw_taken", 0) == 0


def test_max_min_auto_off(tmp_path):
    app = _app(tmp_path, {"det_frames_max_min": 1})
    app.start()
    time.sleep(0.5)
    app._window_open_at -= 61                # 撮影窓が開いて 61 秒たったことにする
    time.sleep(0.5)
    taken = app.c.snapshot()["raw_taken"]
    time.sleep(0.5)
    app.request_stop(); app.shutdown()
    assert app.c.snapshot()["raw_taken"] == taken   # 止まっている
    assert any(n["note"] == "det_frames auto-off" for n in app._notes)
