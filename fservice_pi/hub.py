"""個室の小型カメラ（ESP32）からの受け口。店の Pi 5 で常に動かす（fservice-hub）。

店内の Wi-Fi だけで使う。インターネットには出さない。

  POST /v1/clip?cam=<部屋>   1 回の撮影分の写真（下の形式）。部屋の受け取り箱（inbox）に 1 枚ずつ置く
  POST /v1/still?cam=<部屋>  5 分ごとの静止画（JPEG）。X-Status ヘッダにカメラの状態（JSON）
  GET  /v1/state?cam=<部屋>  撮ってよいか（部屋の端末が書く state.json）と今の時刻
  GET  /                     部屋ごとの一覧（設置のときにスマホのブラウザで見る）・新しいプログラムを配る
  GET  /still/<部屋>.jpg     最後の静止画
  GET  /v1/fw.bin?cam=<部屋>  カメラに配るプログラム（Wi-Fi 経由の更新。key はクエリでも可）
  POST /v1/fw?cam=<部屋|all>  配るプログラムを置く（一覧ページから。中身は Arduino IDE が出力した .bin）

配るプログラムの版は、.bin の中の "fscam-x.y.z" を読んで決める。カメラは「撮ってよいか」の返事に
入っている版 "fw" が自分と違えば、撮影していないときに取りに来て更新し、再起動する。
部屋ごとに置いたもの（rooms/<部屋>/fw.bin）が全体用（firmware/fscam.bin）より優先する
（まず 1 部屋で試してから全部屋へ、ができるように）。

撮影の形式: 写真ごとに "FSF1"（4 バイト）＋撮った時刻（UNIX ミリ秒・8 バイト・リトルエンディアン）
＋JPEG の長さ（4 バイト・リトルエンディアン）＋JPEG を並べたもの。

どの返事にも Pi 5 の時刻 "t"（UNIX ミリ秒）を入れる（カメラの時刻合わせ用）。
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import os
import re
import shutil
import struct
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

ROOMS_DIR = "/var/lib/fservice-pi/rooms"
KEY_FILE = "/etc/fservice-pi/hub.key"
PORT = 8650
MAGIC = b"FSF1"
HEAD = struct.Struct("<4sQI")
FRAME_MAX = 1024 * 1024        # 1 枚の上限
CLIP_MAX = 64 * 1024 * 1024    # 1 回の撮影の上限
STILL_MAX = 1024 * 1024
INBOX_MAX = 20000              # 受け取り箱がこれ以上なら「後で送って」
MIN_FREE_MB = 2048
CAM_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
T_MIN = 1_600_000_000_000      # これより前の時刻は「時刻が合っていない」写真
FW_MAX = 3 * 1024 * 1024       # アプリ領域（3MB）に入る大きさまで
FW_RE = re.compile(rb"fscam-\d+\.\d+\.\d+")


def fw_version(data: bytes) -> str:
    """ESP32 のプログラム（.bin）から版を読む。ESP32 のプログラムでない・版が無ければ空。"""
    if len(data) < 1024 or data[0] != 0xE9:  # ESP32 のプログラムは 0xE9 で始まる
        return ""
    m = FW_RE.search(data)
    return m.group(0).decode() if m else ""


def now_ms() -> int:
    return time.time_ns() // 1_000_000


class ClipError(ValueError):
    pass


def read_clip(stream, length: int):
    """撮影の形式を 1 枚ずつ読む。(時刻, JPEG) を返す。壊れていれば ClipError。"""
    left = length
    while left > 0:
        if left < HEAD.size:
            raise ClipError("末尾が途中で切れている")
        magic, t, n = HEAD.unpack(_read_exact(stream, HEAD.size))
        left -= HEAD.size
        if magic != MAGIC:
            raise ClipError("形式が違う")
        if n == 0 or n > FRAME_MAX or n > left:
            raise ClipError(f"写真の長さがおかしい: {n}")
        data = _read_exact(stream, n)
        left -= n
        yield t, data


def _read_exact(stream, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            raise ClipError("途中で切れた")
        buf += chunk
    return bytes(buf)


class Hub:
    def __init__(self, rooms_dir: str | Path = ROOMS_DIR, key: str = ""):
        self.rooms = Path(rooms_dir)
        self.key = key
        self._fw_cache: dict = {}  # 配るプログラムの版と MD5（毎回 .bin を読み直さないため）

    def room(self, cam: str) -> Path | None:
        """登録済みの部屋のフォルダ。知らない部屋名なら None（add-room.sh で作った部屋だけ受け取る）。"""
        if not CAM_RE.match(cam or ""):
            return None
        d = self.rooms / cam
        return d if (d / "inbox").is_dir() else None

    def free_mb(self) -> int:
        try:
            return shutil.disk_usage(self.rooms).free // (1024 * 1024)
        except OSError:
            return 0

    def busy(self, room: Path) -> str:
        """受け取れない理由。受け取れれば空。"""
        if self.free_mb() < MIN_FREE_MB:
            return "ディスクの空きが少ない"
        try:
            n = sum(1 for _ in os.scandir(room / "inbox"))
        except OSError:
            n = 0
        if n >= INBOX_MAX:
            return "受け取り箱がいっぱい"
        return ""

    def save_clip(self, room: Path, stream, length: int) -> tuple[int, int, str]:
        """(置いた枚数, すでにあった枚数, 壊れていた理由) を返す。壊れていても読めた分は置く。"""
        inbox = room / "inbox"
        saved = dup = 0
        err = ""
        try:
            for t, jpg in read_clip(stream, length):
                if t < T_MIN or jpg[:2] != b"\xff\xd8":
                    continue
                path = inbox / f"{t}.jpg"
                if path.exists():
                    dup += 1
                    continue
                tmp = inbox / f".{t}.tmp"
                tmp.write_bytes(jpg)
                os.replace(tmp, path)
                saved += 1
        except ClipError as e:
            err = str(e)
        return saved, dup, err

    def save_still(self, room: Path, jpg: bytes, status: dict):
        if jpg[:2] == b"\xff\xd8":
            tmp = room / ".still.tmp"
            tmp.write_bytes(jpg)
            os.replace(tmp, room / "still.jpg")
        status = dict(status)
        status["seen"] = now_ms()
        tmp = room / ".status.tmp"
        tmp.write_text(json.dumps(status, ensure_ascii=False))
        os.replace(tmp, room / "status.json")

    def state(self, room: Path) -> dict:
        """部屋の端末が書いた state.json。無い・古い（端末が動いていない）なら撮らない。
        配るプログラムがあれば、その版 "fw" も入れる（カメラが自分と違えば更新する）。"""
        out = self._capture_state(room)
        fw = self.firmware(room)
        if fw:
            out["fw"] = fw[1]
        return out

    def _capture_state(self, room: Path) -> dict:
        try:
            st = json.loads((room / "state.json").read_text())
            if now_ms() - int(st.get("t", 0)) > 10 * 60_000:
                return {"capture": False, "why": "部屋の端末が動いていない"}
            return {"capture": bool(st.get("capture")), "why": st.get("why", "")}
        except (OSError, ValueError):
            return {"capture": False, "why": "部屋の端末がまだ動いていない"}

    @property
    def fw_dir(self) -> Path:
        return self.rooms.parent / "firmware"

    def firmware(self, room: Path) -> tuple[Path, str, str] | None:
        """この部屋に配るプログラム (場所, 版, MD5)。無ければ None。部屋ごとのものが優先。"""
        for path in (room / "fw.bin", self.fw_dir / "fscam.bin"):
            try:
                st = path.stat()
            except OSError:
                continue
            key = (str(path), st.st_mtime_ns, st.st_size)
            hit = self._fw_cache.get(str(path))
            if hit and hit[0] == key:
                return hit[1]
            data = path.read_bytes()
            ver = fw_version(data)
            info = (path, ver, hashlib.md5(data).hexdigest()) if ver else None
            self._fw_cache[str(path)] = (key, info)
            if info:
                return info
        return None

    def put_firmware(self, target: str, data: bytes) -> tuple[str, str]:
        """配るプログラムを置く。target は部屋名か "all"。(版, 置いた場所) を返す。だめなら ValueError。"""
        ver = fw_version(data)
        if not ver:
            raise ValueError("個室カメラのプログラム（fscam の .bin）ではない")
        if target == "all":
            dest = self.fw_dir / "fscam.bin"
            dest.parent.mkdir(parents=True, exist_ok=True)
            # 全部屋に配るときは、試しに部屋ごとに置いたものを片付ける（古い版が優先されないように）
            for name in self.room_names():
                try:
                    (self.rooms / name / "fw.bin").unlink()
                except OSError:
                    pass
        else:
            room = self.room(target)
            if room is None:
                raise ValueError(f"知らない部屋: {target}")
            dest = room / "fw.bin"
        tmp = dest.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
        return ver, str(dest)

    def room_names(self) -> list[str]:
        try:
            return sorted(p.name for p in self.rooms.iterdir() if (p / "inbox").is_dir())
        except OSError:
            return []

    def listing(self) -> list[dict]:
        out = []
        for name in self.room_names():
            room = self.rooms / name
            try:
                st = json.loads((room / "status.json").read_text())
            except (OSError, ValueError):
                st = {}
            try:
                inbox = sum(1 for e in os.scandir(room / "inbox") if e.name.endswith(".jpg"))
            except OSError:
                inbox = 0
            fw = self.firmware(room)
            out.append({"cam": name, "status": st, "state": self.state(room), "inbox": inbox,
                        "still": (room / "still.jpg").exists(), "fw_want": fw[1] if fw else ""})
        return out


def _ago(ms: int | None) -> str:
    if not ms:
        return "まだ連絡なし"
    s = max(0, (now_ms() - ms) // 1000)
    if s < 60:
        return f"{s} 秒前"
    if s < 3600:
        return f"{s // 60} 分前"
    return f"{s // 3600} 時間前"


def _rssi_label(rssi) -> str:
    try:
        r = int(rssi)
    except (TypeError, ValueError):
        return "—"
    word = "良い" if r > -70 else ("ふつう" if r > -80 else "弱い")
    return f"{r}（{word}）"


def render_index(rows: list[dict]) -> str:
    trs = []
    for r in rows:
        st = r["status"]
        seen = st.get("seen")
        late = not seen or now_ms() - seen > 12 * 60_000
        cells = [
            html.escape(r["cam"]),
            ('<span class="ng">' if late else "") + _ago(seen) + ("</span>" if late else ""),
            _rssi_label(st.get("rssi")),
            html.escape(str(st.get("temp", "—"))),
            html.escape(str(st.get("boots", "—"))) + " / " + html.escape(str(st.get("reset", "—"))),
            html.escape(f"カメラ {st.get('cam_err', 0)}・SD {st.get('sd_err', 0)}・Wi-Fi {st.get('wifi_err', 0)}"),
            html.escape(str(st.get("pending", "—"))),
            "撮る" if r["state"]["capture"] else html.escape("撮らない（" + r["state"]["why"] + "）"),
            str(r["inbox"]),
            f'<a href="/still/{html.escape(r["cam"])}.jpg">見る</a>' if r["still"] else "—",
            _fw_cell(st.get("fw", ""), r.get("fw_want", "")),
        ]
        trs.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    head = ["部屋", "最後の連絡", "電波", "温度", "起動回数 / 最後の再起動の理由", "失敗の回数",
            "未送信", "撮影", "処理待ち", "写真", "プログラムの版"]
    options = "".join(f'<option value="{html.escape(r["cam"])}">{html.escape(r["cam"])} だけ（試す）</option>'
                      for r in rows)
    return f"""<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="30">
<title>個室カメラ</title><style>
body{{font-family:sans-serif;margin:12px}} table{{border-collapse:collapse;font-size:14px}}
td,th{{border:1px solid #ccc;padding:4px 6px;text-align:left}} .ng{{color:#c00;font-weight:bold}}
</style></head><body><h1>個室カメラ</h1><p>30 秒ごとに更新。電波は -70 より大きければ安定、-80 より小さいと途切れやすい。</p>
<table><tr>{"".join(f"<th>{h}</th>" for h in head)}</tr>{"".join(trs) or "<tr><td colspan=11>部屋がまだありません（add-room.sh で足す）</td></tr>"}</table>
<h2>新しいプログラムを配る（Wi-Fi 経由の更新）</h2>
<p>Arduino IDE の「スケッチ」→「コンパイルしたバイナリを出力」でできた <b>fscam.ino.bin</b> を選ぶ。
カメラは撮影していないときに 1 分ほどで取りに来て、更新して再起動する。<b>まず 1 部屋で試してから</b>全部屋へ。</p>
<p><input type="file" id="fw" accept=".bin"> 配る先 <select id="to">{options}<option value="all">全部屋</option></select>
合言葉 <input type="password" id="key" size="26"> <button onclick="send()">配る</button> <span id="msg"></span></p>
<script>
async function send() {{
  const f = document.getElementById('fw').files[0], msg = document.getElementById('msg');
  if (!f) {{ msg.textContent = 'ファイルを選んでください'; return; }}
  msg.textContent = '送っています…';
  const r = await fetch('/v1/fw?cam=' + encodeURIComponent(document.getElementById('to').value),
    {{method: 'POST', headers: {{'X-Key': document.getElementById('key').value}}, body: f}});
  const j = await r.json();
  msg.textContent = j.ok ? ('置きました: ' + j.version + '（' + j.to + '）') : ('できませんでした: ' + j.why);
}}
</script>
</body></html>"""


def _fw_cell(now: str, want: str) -> str:
    now = now or "—"
    if want and want != now:
        return html.escape(f"{now} → {want}（更新待ち）")
    return html.escape(now)


def make_handler(hub: Hub):
    class Handler(BaseHTTPRequestHandler):
        server_version = "fservice-hub"

        def log_message(self, fmt, *args):
            log.debug("%s " + fmt, self.address_string(), *args)

        def _json(self, code: int, body: dict):
            body = dict(body)
            body["t"] = now_ms()
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _room(self):
            q = parse_qs(urlparse(self.path).query)
            cam = (q.get("cam") or [""])[0]
            # Wi-Fi 経由の更新（Arduino の HTTPUpdate）はヘッダを足せないので、合言葉はクエリでも受ける
            given = self.headers.get("X-Key", "") or (q.get("key") or [""])[0]
            if hub.key and given != hub.key:
                self._json(403, {"ok": False, "why": "合言葉が違う"})
                return None, cam
            room = hub.room(cam)
            if room is None:
                self._json(404, {"ok": False, "why": f"知らない部屋: {cam}"})
                return None, cam
            return room, cam

        def _length(self, limit: int) -> int | None:
            try:
                n = int(self.headers.get("Content-Length", ""))
            except ValueError:
                self._json(411, {"ok": False, "why": "長さが無い"})
                return None
            if n < 0 or n > limit:
                self._json(413, {"ok": False, "why": "大きすぎる"})
                return None
            return n

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/v1/state":
                room, cam = self._room()
                if room is not None:
                    self._json(200, {"ok": True, **hub.state(room)})
                return
            if path == "/":
                data = render_index(hub.listing()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/v1/fw.bin":
                room, cam = self._room()
                if room is None:
                    return
                fw = hub.firmware(room)
                if not fw:
                    self._json(404, {"ok": False, "why": "配るプログラムが無い"})
                    return
                data = fw[0].read_bytes()
                log.info("%s: プログラム %s を配る", cam, fw[1])
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("x-MD5", fw[2])  # HTTPUpdate が書き込み後に確かめる
                self.end_headers()
                self.wfile.write(data)
                return
            m = re.match(r"^/still/([a-z0-9-]+)\.jpg$", path)
            if m:
                room = hub.room(m.group(1))
                try:
                    data = (room / "still.jpg").read_bytes() if room else b""
                except OSError:
                    data = b""
                if data:
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            self._json(404, {"ok": False})

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/v1/clip":
                room, cam = self._room()
                if room is None:
                    return
                n = self._length(CLIP_MAX)
                if n is None:
                    return
                why = hub.busy(room)
                if why:
                    self.rfile.read(n)
                    self._json(503, {"ok": False, "why": why})
                    return
                saved, dup, err = hub.save_clip(room, self.rfile, n)
                if err:
                    log.warning("%s: 壊れた撮影を受け取った（%d 枚は置いた）: %s", cam, saved, err)
                    self._json(400, {"ok": False, "saved": saved, "skipped": dup, "why": err})
                    return
                log.info("%s: 写真 %d 枚を受け取った（重複 %d）", cam, saved, dup)
                self._json(200, {"ok": True, "saved": saved, "skipped": dup})
                return
            if path == "/v1/still":
                room, cam = self._room()
                if room is None:
                    return
                n = self._length(STILL_MAX)
                if n is None:
                    return
                jpg = self.rfile.read(n)
                try:
                    status = json.loads(self.headers.get("X-Status", "") or "{}")
                    if not isinstance(status, dict):
                        status = {}
                except ValueError:
                    status = {}
                hub.save_still(room, jpg, status)
                self._json(200, {"ok": True, **hub.state(room)})
                return
            if path == "/v1/fw":
                q = parse_qs(urlparse(self.path).query)
                target = (q.get("cam") or [""])[0]
                if hub.key and self.headers.get("X-Key", "") != hub.key:
                    self._json(403, {"ok": False, "why": "合言葉が違う"})
                    return
                n = self._length(FW_MAX)
                if n is None:
                    return
                try:
                    ver, dest = hub.put_firmware(target, self.rfile.read(n))
                except ValueError as e:
                    self._json(400, {"ok": False, "why": str(e)})
                    return
                log.info("配るプログラムを置いた: %s → %s", ver, dest)
                self._json(200, {"ok": True, "version": ver, "to": "全部屋" if target == "all" else target})
                return
            self._json(404, {"ok": False})

    return Handler


def load_key(path: str = KEY_FILE) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def main(port: int = PORT, rooms_dir: str = ROOMS_DIR, key_file: str = KEY_FILE) -> int:
    key = load_key(key_file)
    if not key:
        log.warning("合言葉（%s）が無い。誰からでも受け取る", key_file)
    hub = Hub(rooms_dir, key)
    Path(rooms_dir).mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(hub))
    srv.daemon_threads = True
    log.info("個室カメラの受け口を開いた: ポート %d（部屋 %s）", port, rooms_dir)
    try:
        srv.serve_forever()
    finally:
        srv.server_close()
    return 0
