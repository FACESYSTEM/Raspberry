import io
import zipfile

from fservice_pi.api import Sent
from fservice_pi.motion import MotionLog
from fservice_pi.stats import Counters
from fservice_pi.uploader import BATCH_FRAMES, ENTRY_MAX, Batcher, Outbox, Sender, build_zip, Item


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(tmp_path, mode, min_free_mb=0):
    c = Counters()
    m = MotionLog()
    ob = Outbox(tmp_path / "outbox", tmp_path / "failed", min_free_mb, c)
    clock = Clock()
    b = Batcher(m, ob, c, lambda: mode, clock=clock)
    return c, m, ob, b, clock


def zip_names(path):
    with zipfile.ZipFile(path) as z:
        return z.namelist()


def test_build_zip_names_and_stored():
    body = build_zip([Item(1000, b"a"), Item(1000, b"b"), Item(2000, b"c")])
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        assert z.namelist() == ["1000r.jpg", "1001r.jpg", "2000r.jpg"]
        assert all(i.compress_type == zipfile.ZIP_STORED for i in z.infolist())


def test_mode2_drops_unmanned_and_keeps_guard(tmp_path):
    c, m, ob, b, clock = make(tmp_path, mode=2)
    # 0..20 秒・100ms ごと。動きは 0 秒（先頭）だけ
    for t in range(0, 20_001, 100):
        m.record(t, t == 0)
        b.add(t, b"x" * 100)
    b.flush(force=True)
    snap = c.snapshot()
    kept = sum(len(zip_names(p)) for p in ob.root.glob("*.zip"))
    # 0〜5 秒（51 枚）は残る。5.1〜15 秒（100 枚）は落とす。15 秒より後（50 枚）は
    # 保護帯が確定しないまま止めたので送る側に倒す
    assert kept + snap["diff_dropped"] == 201
    assert snap["diff_dropped"] == 100
    assert kept == 101


def test_mode1_shadow_sends_everything(tmp_path):
    c, m, ob, b, clock = make(tmp_path, mode=1)
    for t in range(0, 20_001, 100):
        m.record(t, t == 0)
        b.add(t, b"x")
    b.flush(force=True)
    kept = sum(len(zip_names(p)) for p in ob.root.glob("*.zip"))
    assert kept == 201
    assert c.snapshot()["diff_would_drop"] == 100


def test_mode0_sends_without_waiting(tmp_path):
    c, m, ob, b, clock = make(tmp_path, mode=0)
    for t in range(0, BATCH_FRAMES * 100, 100):
        b.add(t, b"x")  # 動きの記録が無くても待たない
    files = list(ob.root.glob("*.zip"))
    assert len(files) == 1 and files[0].name == f"0-{BATCH_FRAMES}.zip"


def test_batch_cut_by_time(tmp_path):
    c, m, ob, b, clock = make(tmp_path, mode=0)
    b.add(1, b"x")
    b.flush()
    assert not list(ob.root.glob("*.zip"))
    clock.t = 10.0
    b.flush()
    assert len(list(ob.root.glob("*.zip"))) == 1


def test_oversized_entry_is_counted(tmp_path):
    c, m, ob, b, clock = make(tmp_path, mode=0)
    b.add(1, b"x" * (ENTRY_MAX + 1))
    b.flush(force=True)
    assert c.snapshot()["too_big_dropped"] == 1


def test_outbox_refuses_when_disk_low(tmp_path):
    c, m, ob, b, clock = make(tmp_path, mode=0, min_free_mb=10**9)
    b.add(1, b"x")
    b.flush(force=True)
    assert not list(ob.root.glob("*.zip"))
    assert c.snapshot()["outbox_full_dropped"] == 1


class FakeApi:
    enabled = True
    ready = True

    def __init__(self, results):
        self.results = list(results)
        self.bodies = []

    def post_detframes(self, body):
        self.bodies.append(body)
        r = self.results.pop(0) if self.results else (Sent.OK, "")
        return (*r, {"ok": True, "saved": 1, "skipped": 0} if r[0] is Sent.OK else {})


def test_sender_retry_then_ok_and_reject(tmp_path):
    import time
    c = Counters()
    ob = Outbox(tmp_path / "outbox", tmp_path / "failed", 0, c)
    ob.put([Item(1, b"a"), Item(2, b"b")])
    ob.put([Item(5, b"c")])
    api = FakeApi([(Sent.RETRY, "down"), (Sent.OK, ""), (Sent.REJECT, "HTTP 400 bad")])
    s = Sender(api, ob, c)
    s.backoff_start = 0.01
    s.start()
    deadline = time.monotonic() + 5
    while ob.oldest() is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    s.stop()
    assert len(api.bodies) == 3  # 1 回目は送り直し
    assert c.snapshot()["raw_sent"] == 2
    assert c.snapshot()["send_retry"] == 1
    assert (tmp_path / "failed" / "5-1.zip").exists()
    assert (tmp_path / "failed" / "5-1.zip.reason.txt").read_text() == "HTTP 400 bad"
