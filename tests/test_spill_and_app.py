import time
import zipfile

import numpy as np

from fservice_pi.app import App, encode_jpeg, local_hours
from fservice_pi.camera import Frame
from fservice_pi.config import Config
from fservice_pi.spill import Spill
from fservice_pi.uploader import ENTRY_MAX


def test_spill_roundtrip_and_recovery(tmp_path):
    s = Spill(tmp_path, min_free_mb=0)
    s.start()
    for t in (300, 100, 200):
        bgr = np.full((64, 64, 3), t % 256, np.uint8)
        assert s.offer(Frame(t, bgr, bgr[:, :, 0]))
    s.stop()
    assert s.stats()["spill_now"] == 3

    s = Spill(tmp_path, min_free_mb=0)
    s.start()
    t, bgr, path = s.take()
    assert t == 100 and bgr[0, 0, 0] == 100
    # 処理中に落ちた想定 → 次の起動で戻る
    s2 = Spill(tmp_path, min_free_mb=0)
    s2.start()
    assert s2.stats()["spill_now"] == 3
    t, bgr, path = s2.take()
    s2.done(path)
    assert s2.stats()["spill_now"] == 2
    s2.stop()
    s.stop()


def test_spill_refuses_when_disk_low(tmp_path):
    s = Spill(tmp_path, min_free_mb=10**9)
    s.start()
    bgr = np.zeros((8, 8, 3), np.uint8)
    assert s.offer(Frame(1, bgr, bgr[:, :, 0])) is False
    s.stop()


def test_local_windows_every_day():
    import datetime as dt
    h = local_hours(["10:55-21:15", "23:00-02:00"])
    d = dt.datetime(2026, 10, 1)
    assert h.open_at(d.replace(hour=11))
    assert not h.open_at(d.replace(hour=22))
    assert h.open_at(d.replace(hour=23, minute=30))
    assert h.open_at(d.replace(hour=1))
    assert not h.open_at(d.replace(hour=3))
    assert local_hours([]).open_at(d.replace(hour=3))


def test_encode_jpeg_respects_entry_limit():
    rng = np.random.default_rng(0)
    noise = rng.integers(0, 256, (640, 640, 3), dtype=np.uint8)  # 縮まない絵
    hi = encode_jpeg(noise, 100)
    # 縮まない絵は画質を下げて作り直している（上限に入るか、画質 20 まで下げ切った）
    assert hi[:2] == b"\xff\xd8"
    assert len(hi) <= ENTRY_MAX or len(hi) <= len(encode_jpeg(noise, 20)) * 1.01
    flat = np.full((640, 640, 3), 128, np.uint8)
    assert len(encode_jpeg(flat, 60)) <= ENTRY_MAX


def test_app_end_to_end_with_fake_camera(tmp_path):
    cfg = Config()
    cfg.camera.kind = "fake"
    cfg.camera.fps = 30
    cfg.camera.long_px = 160
    cfg.capture.raw_diff_filter = 0  # 全部送る（保護帯の待ちを入れずに中身を確かめる）
    cfg.storage.root = str(tmp_path)
    cfg.storage.min_free_mb = 0
    app = App(cfg)
    app.start()
    time.sleep(2.5)
    app.request_stop()
    app.shutdown()
    snap = app.c.snapshot()
    files = list((tmp_path / "outbox").glob("*.zip"))
    names = []
    for p in files:
        with zipfile.ZipFile(p) as z:
            names += z.namelist()
    assert snap["raw_frames"] >= 40
    # 撮れたコマが 1 枚も欠けずに送信待ちへ入っている
    assert len(names) == snap["raw_frames"] - snap.get("raw_dropped", 0)
    assert all(n.endswith("r.jpg") for n in names)
