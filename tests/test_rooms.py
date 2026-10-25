import io
import json
import struct
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

import cv2
import numpy as np
import pytest

from fservice_pi import hub as hubmod
from fservice_pi.app import App
from fservice_pi.camera import InboxCamera
from fservice_pi.config import CameraConfig, Config
from fservice_pi.rooms import load_rooms

T0 = 1_790_000_000_000


def jpeg(v=128, w=320, h=240):
    img = np.full((h, w, 3), v, np.uint8)
    img[10:60, 10:60] = 255 - v
    return cv2.imencode(".jpg", img)[1].tobytes()


def clip(frames):
    return b"".join(struct.pack("<4sQI", b"FSF1", t, len(j)) + j for t, j in frames)


@pytest.fixture
def server(tmp_path):
    rooms = tmp_path / "rooms"
    (rooms / "room1" / "inbox").mkdir(parents=True)
    h = hubmod.Hub(rooms, key="k")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), hubmod.make_handler(h))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", rooms, h
    srv.shutdown()


def call(url, data=None, key="k", headers=None):
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    if key:
        req.add_header("X-Key", key)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_read_clip_roundtrip_and_errors():
    body = clip([(T0, b"\xff\xd8a"), (T0 + 1, b"\xff\xd8bb")])
    assert list(hubmod.read_clip(io.BytesIO(body), len(body))) == [(T0, b"\xff\xd8a"), (T0 + 1, b"\xff\xd8bb")]
    with pytest.raises(hubmod.ClipError):
        list(hubmod.read_clip(io.BytesIO(body[:-1]), len(body) - 1))
    bad = b"XXXX" + body[4:]
    with pytest.raises(hubmod.ClipError):
        list(hubmod.read_clip(io.BytesIO(bad), len(bad)))


def test_clip_saved_once_and_time_returned(server):
    base, rooms, _ = server
    body = clip([(T0, jpeg()), (T0 + 400, jpeg(60)), (5, jpeg())])  # 時刻の合っていない 1 枚は捨てる
    code, out = call(f"{base}/v1/clip?cam=room1", body)
    res = json.loads(out)
    assert code == 200 and res["ok"] and res["saved"] == 2 and res["t"] > T0
    code, out = call(f"{base}/v1/clip?cam=room1", body)
    assert json.loads(out)["skipped"] == 2                       # 送り直しても二重にならない
    assert sorted(p.name for p in (rooms / "room1" / "inbox").iterdir()) == [f"{T0}.jpg", f"{T0 + 400}.jpg"]


def test_rejects_wrong_key_unknown_room_and_broken(server):
    base, rooms, _ = server
    body = clip([(T0, jpeg())])
    assert call(f"{base}/v1/clip?cam=room1", body, key="x")[0] == 403
    assert call(f"{base}/v1/clip?cam=room9", body)[0] == 404
    assert call(f"{base}/v1/clip?cam=../etc", body)[0] == 404
    code, out = call(f"{base}/v1/clip?cam=room1", body + b"FSF1")
    assert code == 400 and json.loads(out)["saved"] == 1         # 読めた分は置く


def test_busy_when_disk_low(server, monkeypatch):
    base, rooms, h = server
    monkeypatch.setattr(h, "free_mb", lambda: 10)
    code, out = call(f"{base}/v1/clip?cam=room1", clip([(T0, jpeg())]))
    assert code == 503 and not list((rooms / "room1" / "inbox").iterdir())


def test_state_still_and_index(server):
    base, rooms, _ = server
    code, out = call(f"{base}/v1/state?cam=room1")
    assert code == 200 and json.loads(out)["capture"] is False    # 部屋の端末がまだ書いていない
    (rooms / "room1" / "state.json").write_text(json.dumps({"capture": True, "t": hubmod.now_ms()}))
    code, out = call(f"{base}/v1/still?cam=room1", jpeg(),
                     headers={"X-Status": json.dumps({"rssi": -62, "boots": 3})})
    assert code == 200 and json.loads(out)["capture"] is True
    st = json.loads((rooms / "room1" / "status.json").read_text())
    assert st["rssi"] == -62 and st["seen"] > 0
    code, page = call(f"{base}/", key="")
    assert code == 200 and "room1" in page.decode() and "-62（良い）" in page.decode()
    assert call(f"{base}/still/room1.jpg", key="")[1][:2] == b"\xff\xd8"


def test_inbox_camera_reads_in_time_order(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    for t in (T0 + 2000, T0, T0 + 1000):
        (inbox / f"{t}.jpg").write_bytes(jpeg(t % 200))
    (inbox / f"{T0 + 3000}.jpg").write_bytes(b"broken")
    cam = InboxCamera(CameraConfig(kind="inbox", device=str(inbox), long_px=160))
    got = []
    while (f := cam.read()) is not None:
        got.append(f.t_ms)
        assert f.bgr.shape == (120, 160, 3)
    assert got == [T0, T0 + 1000, T0 + 2000] and not list(inbox.iterdir())


def test_room_app_sends_every_pushed_frame(tmp_path):
    root = tmp_path / "room1"
    (root / "inbox").mkdir(parents=True)
    cfg = Config()
    cfg.camera.kind = "inbox"
    cfg.camera.device = str(root / "inbox")
    cfg.camera.long_px = 160
    cfg.capture.raw_diff_filter = 2          # サーバ側の設定で間引きが入っていても
    cfg.storage.root = str(root)
    cfg.storage.min_free_mb = 0
    app = App(cfg, label="room1")
    # 古い撮影（数日前）でも、順番が前後しても全部流す
    ts = [T0 + i * 400 for i in range(25)] + [T0 - 86_400_000 + i * 400 for i in range(5)]
    for t in ts:
        (root / "inbox" / f"{t}.jpg").write_bytes(jpeg(t % 250))
    app.start()
    deadline = time.time() + 15
    while time.time() < deadline and app.c.snapshot().get("raw_taken", 0) < len(ts):
        time.sleep(0.1)
    time.sleep(0.5)
    app.request_stop()
    app.shutdown()
    names = []
    for p in (root / "outbox").glob("*.zip"):
        with zipfile.ZipFile(p) as z:
            names += z.namelist()
    sent = sorted(int(n[:-5]) for n in names)
    assert app.c.snapshot()["raw_taken"] == len(ts)
    assert sent == sorted(ts)                # 店の設定で間引きが入っていても、1 枚も削らない
    state = json.loads((root / "state.json").read_text())
    assert state["capture"] is True


def test_load_rooms_skips_bad_configs(tmp_path):
    (tmp_path / "room1.toml").write_text(
        '[camera]\nkind = "inbox"\ndevice = "/tmp/x/inbox"\n[storage]\nroot = "/tmp/x"\n')
    (tmp_path / "room2.toml").write_text('[camera]\nkind = "opencv"\n')
    (tmp_path / "room3.toml").write_text('[nope]\n')
    apps = load_rooms(tmp_path)
    assert [a.label for a in apps] == ["room1"]


def fake_fw(ver="fscam-0.2.0", size=4096):
    return bytes([0xE9]) + b"\0" * 100 + ver.encode() + b"\0" * (size - 101 - len(ver))


def test_fw_version_reads_bin():
    assert hubmod.fw_version(fake_fw("fscam-1.2.3")) == "fscam-1.2.3"
    assert hubmod.fw_version(b"\x00" + fake_fw()[1:]) == ""      # ESP32 のプログラムではない
    assert hubmod.fw_version(fake_fw()[:100]) == ""


def test_fw_upload_room_first_then_all(server):
    base, rooms, _ = server
    (rooms / "room2" / "inbox").mkdir(parents=True)
    assert "fw" not in json.loads(call(f"{base}/v1/state?cam=room1")[1])
    assert call(f"{base}/v1/fw?cam=room1", fake_fw(), key="x")[0] == 403
    assert call(f"{base}/v1/fw?cam=room1", b"not a firmware" * 100)[0] == 400
    # まず 1 部屋で試す
    code, out = call(f"{base}/v1/fw?cam=room1", fake_fw("fscam-0.3.0"))
    assert code == 200 and json.loads(out)["version"] == "fscam-0.3.0"
    assert json.loads(call(f"{base}/v1/state?cam=room1")[1])["fw"] == "fscam-0.3.0"
    assert "fw" not in json.loads(call(f"{base}/v1/state?cam=room2")[1])
    # カメラは合言葉をクエリで渡して取りに来る（HTTPUpdate はヘッダを足せない）
    req = urllib.request.Request(f"{base}/v1/fw.bin?cam=room1&key=k")
    with urllib.request.urlopen(req, timeout=5) as r:
        data = r.read()
        assert r.headers["x-MD5"] == __import__("hashlib").md5(data).hexdigest()
    assert data == fake_fw("fscam-0.3.0")
    assert call(f"{base}/v1/fw.bin?cam=room1", key="")[0] == 403
    # 全部屋に配ると、試しの版は片付いて全部屋が同じ版になる
    assert call(f"{base}/v1/fw?cam=all", fake_fw("fscam-0.4.0"))[0] == 200
    for room in ("room1", "room2"):
        assert json.loads(call(f"{base}/v1/state?cam={room}")[1])["fw"] == "fscam-0.4.0"
    assert "0.4.0" in call(f"{base}/", key="")[1].decode()
