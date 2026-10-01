"""本体。スレッドの組み立て。

  撮影スレッド  カメラ → 長辺 640 に縮める → 動きの判定 → 圧縮待ちへ（満杯ならディスクへ逃がす）
  圧縮スレッド  圧縮待ち（空ならディスクの残り）→ JPEG → 束ね
  送信スレッド  送信待ち（ディスク）→ POST /v1/detframes
  心拍スレッド  名乗り（/v1/announce）→ GET /v1/config（撮影窓・face_enabled・face_params）
  見張り        束ねの締め・申告・systemd のウォッチドッグ
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import platform
import queue
import socket
import threading
import time

import cv2

from . import VERSION, VERSION_CODE, hours
from .api import Sent, ServerApi
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


def local_hours(windows: list[str]) -> hours.Hours:
    """設定の capture.windows（毎日同じ時間帯）。サーバから撮影窓が来るまでの仮のもの。"""
    if not windows:
        return hours.Hours(None)
    return hours.parse("1234567 " + " ".join(windows))


def terminal_id(configured: str) -> str:
    if configured:
        return configured
    try:
        with open("/etc/machine-id") as f:
            return "pi-" + f.read().strip()[:12]
    except OSError:
        return "pi-" + platform.node()


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def system_telemetry() -> dict:
    """OS 側の状態（Api.kt Telemetry と同じ名前で送るもの）。取れないものは送らない。"""
    out: dict = {}
    up = _read("/proc/uptime").split()
    if up:
        out["uptime"] = int(float(up[0]))
    mem = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        if v.strip().endswith("kB"):
            mem[k] = int(v.split()[0]) // 1024
    if "MemTotal" in mem:
        out["memtotal"] = mem["MemTotal"]
        out["memfree"] = mem.get("MemAvailable", mem.get("MemFree", -1))
        out["memlow"] = str(out["memfree"] < 200).lower()
    for line in _read("/proc/self/status").splitlines():
        if line.startswith("VmRSS:"):
            out["appmem"] = int(line.split()[1]) // 1024
    # Wi-Fi の電波（dBm）。/proc/net/wireless の 4 列目
    for line in _read("/proc/net/wireless").splitlines()[2:]:
        cols = line.split()
        if len(cols) >= 4:
            try:
                out["rssi"] = int(float(cols[3].rstrip(".")))
            except ValueError:
                pass
            break
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # 送らない。経路から自分の IP を知るだけ
            out["ip"] = s.getsockname()[0]
    except OSError:
        pass
    return out


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
        self.spill = Spill(cfg.spill_dir, cfg.storage.min_free_mb)
        self.outbox = Outbox(cfg.outbox_dir, cfg.failed_dir, cfg.storage.min_free_mb, self.c)
        self.batcher = Batcher(self.motion, self.outbox, self.c, self.mode)
        self.sender = Sender(self.api, self.outbox, self.c,
                             may_send=lambda: not (self.defer() and self._capturing))
        self.hours = local_hours(cfg.capture.windows)
        self.face_enabled = True
        self.face_params: dict = {}
        self.store_name = ""
        self.tid = terminal_id(cfg.server.terminal_instance_id)
        self._hb_fails = 0
        self._latest_frame: Frame | None = None
        self._raw_last_ms = 0
        self._window_open_at: float | None = None  # 撮影窓が開いた時刻（det_frames_max_min の起点）
        self._auto_off = False
        self._closed_noted = False
        self._notes: list[dict] = []
        self._frame_wh = (0, 0)
        self._last_selfshot = 0.0
        self._hb_last_err = ""
        self._load_token()
        self._q: queue.Queue[Frame] = queue.Queue(maxsize=cfg.capture.queue_max)
        self._stop = threading.Event()
        self._capture_done = threading.Event()
        self._started = time.monotonic()
        self._capturing = False
        self._last_capture_loop = time.monotonic()
        self._fps_times: list[float] = []
        self._threads: list[threading.Thread] = []

    # --- 設定 ---

    def _load_token(self):
        if self.api.token:
            return
        tok = _read(str(self.cfg.token_path)).strip()
        if tok:
            self.api.token = tok

    def _save_token(self, token: str):
        path = self.cfg.token_path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token)
        os.replace(tmp, path)

    def should_capture(self, when: dt.datetime) -> bool:
        return self.face_enabled and self.hours.open_at(when)

    def mode(self) -> int:
        v = self.face_params.get("raw_diff_filter", self.cfg.capture.raw_diff_filter)
        try:
            v = int(v)
        except (TypeError, ValueError):
            return 0  # 読めない値は「全部送る」側に倒す
        return v if v in (0, 1, 2) else 0

    def _param_int(self, key: str, default: int | None) -> int | None:
        v = self.face_params.get(key)
        if v is None:
            return default
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def raw_fps(self) -> int:
        """face_params det_frames_raw_fps（0〜60）。書かれていなければ設定の capture.raw_fps。"""
        return max(0, min(60, self._param_int("det_frames_raw_fps", self.cfg.capture.raw_fps)))

    def max_ms(self) -> int | None:
        """face_params det_frames_max_min。撮影窓が開いてからこの分数で生コマを止める（安全弁）。
        Android は書かれていなければ 15 分。Pi は書かれているときだけ効かせる。"""
        v = self._param_int("det_frames_max_min", None)
        return None if v is None else max(1, min(24 * 60, v)) * 60_000

    def defer(self) -> bool:
        """face_params det_frames_defer=1 なら、撮影窓の外（閉店後）にまとめて送る。"""
        return self._param_int("det_frames_defer", 0) == 1

    def note(self, text: str):
        """検出ログに 1 行だけ残す（Android の detFramesNote と同じ形）。"""
        w, h = self._frame_wh
        self._notes.append({"t": now_ms(), "kind": "note", "w": w, "h": h, "f": [], "note": text})
        log.info("記録: %s", text)

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
        log.info("起動 %s 端末 %s mode=%d トークン%s", VERSION, self.tid, self.mode(),
                 "あり" if self.api.token else "なし（名乗って割り当てを待つ）")

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
                if not self.should_capture(dt.datetime.now()):
                    if opened:
                        log.info("撮影窓の外（または管理画面で撮影停止）。カメラを止める")
                        self.camera.close()
                        opened = False
                        self._capturing = False
                        self.batcher.flush(force=True)
                    if not self._closed_noted:
                        self._closed_noted = True
                        self.note("det_frames closed-hours")
                    self._window_open_at = None
                    self._stop.wait(5)
                    continue
                if self._window_open_at is None:
                    # 撮影窓が開いた。安全弁の起点をここに置く（Android の checkSchedule と同じ）
                    self._window_open_at = time.monotonic()
                    self._auto_off = False
                    self._closed_noted = False
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
                self._latest_frame = frame
                self._frame_wh = (frame.bgr.shape[1], frame.bgr.shape[0])
                # 生コマを送るか（face_params に従う）。送らないコマも cam_fps と生存確認には数える
                fps = self.raw_fps()
                if fps <= 0:
                    continue
                mx = self.max_ms()
                if mx is not None and (time.monotonic() - self._window_open_at) * 1000 > mx:
                    if not self._auto_off:
                        self._auto_off = True
                        self.note("det_frames auto-off")
                        self.batcher.flush(force=True)
                    continue
                if fps < 30:
                    if frame.t_ms - self._raw_last_ms < 1000 // fps:
                        continue
                self._raw_last_ms = frame.t_ms
                self.c.add("raw_taken")
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
        """心拍のクエリ。名前は Api.kt の config() と同じ。Pi に無いもの（電池・DPC 等）は送らない
        か、Android が「不明」に使う値（-1）で送る。"""
        snap = self.c.snapshot()
        cam = self.cam_fps()
        temp = cpu_temp_c()
        q = {
            "pending": self.outbox.stats()["outbox_frames"],
            "rejected": snap.get("raw_rejected", 0),
            "standby": str(not self._capturing).lower(),
            "version": VERSION,
            "version_code": VERSION_CODE,
            "screen": "pi",
            # サーバはこの値があるときだけ端末状態を記録する。Pi に電池は無い＝-1（不明）・給電中
            "battery": -1,
            "charging": "true",
            "temp": temp if temp is not None else -1.0,
            "face_pending": self.outbox.stats()["outbox_frames"],
            "face_saved": snap.get("raw_sent", 0),
            "face_standby": str(self._capturing).lower(),
            "app_uptime": int(time.monotonic() - self._started),
            # 解析 fps。Pi は届いた全コマを差分判定するので、カメラ供給と同じ
            "fps": f"{cam:.1f}",
            "cfps": f"{cam:.1f}",
            "hbf": self._hb_fails,
            "hbe": self._hb_last_err[:80],
            "drop": snap.get("raw_dropped", 0),
            "net": "wifi",
        }
        q.update(system_telemetry())
        return q

    def _announce(self) -> bool:
        res = self.api.announce(self.tid, f"Raspberry Pi {platform.machine()}",
                                f"{platform.system()} {platform.release()}")
        if res is None:
            return False
        self._save_token(res.token)
        self.api.token = res.token
        self.store_name = res.store_name
        if res.business_hours:
            self.hours = hours.parse(res.business_hours)
        log.info("店に割り当てられた: %s（%s）", res.store_name, res.store_id)
        return True

    def _latest_jpeg(self) -> bytes | None:
        """いま撮れている 1 枚。撮影していない・古い（10 秒超）なら None。"""
        f = self._latest_frame
        if f is None or not self._capturing or now_ms() - f.t_ms > 10_000:
            return None
        try:
            return encode_jpeg(f.bgr, 80)
        except Exception:
            return None

    def _send_selfshot(self):
        """撮影中は selfshot_s ごとに 1 枚。無人で生コマが全部間引かれても、
        サーバの監視が「カメラが生きている」と分かるようにする。"""
        if time.monotonic() - self._last_selfshot < self.cfg.server.selfshot_s:
            return
        jpeg = self._latest_jpeg()
        if jpeg is None:
            return
        result, detail = self.api.post_selfshot(jpeg)
        if result is Sent.OK:
            self._last_selfshot = time.monotonic()
            self.c.add("selfshot_sent")
        else:
            log.warning("生存確認を送れない: %s", detail)

    def _send_preview(self):
        jpeg = self._latest_jpeg()
        if jpeg is None:
            log.info("画角の確認を頼まれたが、撮影していないので送れない")
            return
        result, detail = self.api.post_preview(jpeg)
        if result is Sent.OK:
            log.info("画角の確認に 1 枚送った")
        else:
            log.warning("画角の確認を送れない: %s", detail)

    def _apply_config(self, data: dict):
        bh = data.get("business_hours")
        if isinstance(bh, str):
            new = hours.parse(bh)
            if bh.strip() and new.always:
                log.warning("撮影窓を読めない（常に撮る）: %r", bh)
            self.hours = new
        enabled = bool(data.get("face_enabled", True))
        if enabled != self.face_enabled:
            log.info("撮影のスイッチ（管理画面）: %s", "入" if enabled else "切")
        self.face_enabled = enabled
        fp = data.get("face_params")
        before = self.mode()
        self.face_params = fp if isinstance(fp, dict) else {}
        if self.mode() != before:
            log.info("raw_diff_filter が %d → %d", before, self.mode())
        name = data.get("store_name")
        if name and name != self.store_name:
            log.info("店舗: %s", name)
            self.store_name = name

    def _heartbeat_loop(self):
        announced_log = 0.0
        while not self._stop.is_set():
            if self.api.enabled:
                if not self.api.token:
                    if not self._announce() and time.monotonic() - announced_log > 600:
                        announced_log = time.monotonic()
                        log.info("名乗りました（%s）。管理画面でこの端末を店に割り当ててください", self.tid)
                else:
                    result, body = self.api.heartbeat(self.telemetry())
                    if result is Sent.OK:
                        self._apply_config(body)
                        if body.get("preview_request"):
                            self._send_preview()
                        self._send_selfshot()
                    else:
                        self._hb_fails += 1
                        self._hb_last_err = str(body)
                        log.warning("心拍に失敗: %s", body)
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
        det["raw_fps_set"] = self.raw_fps()
        w, h = self._frame_wh
        return {"t": now_ms(), "kind": "stat", "w": w, "h": h, "f": [], "det_frames": det}

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
                lines = self._notes + self.batcher.take_notes(self._frame_wh) + [line]
                if self.api.ready and self.cfg.server.send_stat:
                    result, detail = self.api.post_detlog(lines)
                    if result.value == "ok":
                        self._notes = []
                    else:
                        log.warning("申告を送れない（記録は次に回す）: %s", detail)
                        self._notes = [x for x in lines if x.get("kind") == "note"][-200:]
                else:
                    self._notes = []
