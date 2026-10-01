"""設定ファイル（TOML）の読み込み。

設定は事務所で書き込んでから出荷する（現地では触らない）。
置き場の既定は /etc/fservice-pi/config.toml。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ServerConfig:
    # 空ならサーバへ送らない（送信待ちに溜めるだけ。事務所での単体確認用）
    base_url: str = ""
    # 端末トークン。空なら /v1/announce で名乗り、管理画面で店に割り当てられたら
    # 返ってきたものを storage.root/token に保存して使う（Android 版の逆向き登録と同じ）
    token: str = ""
    # 端末の名前（管理画面に出る）。空なら "pi-" + machine-id の先頭 12 文字
    terminal_instance_id: str = ""
    # すべての要求に付けるクエリ。通常は要らない（店はトークンからサーバが決める）
    query: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 20.0
    heartbeat_s: float = 30.0
    # 申告（kind:"stat"）を /v1/detlog へ送る間隔
    stat_s: float = 60.0
    # 撮影中に生存確認の 1 枚（/v1/selfshot）を送る間隔
    selfshot_s: float = 600.0
    send_stat: bool = True


@dataclass
class CameraConfig:
    kind: str = "opencv"  # opencv（USB UVC カメラ）/ fake（試験用の作り物）
    device: str = "/dev/video0"
    fourcc: str = "MJPG"
    width: int = 1280
    height: int = 720
    fps: int = 30
    # 長い辺をこの大きさに縮める（縦横比はそのまま・切り抜かない。Android 版と同じ）
    long_px: int = 640
    # これだけの秒数コマが来なければカメラを開き直す
    stall_s: float = 10.0


@dataclass
class CaptureConfig:
    jpeg_quality: int = 60
    # face_params に書かれていないときの値（サーバに書かれていればそちらに従う）
    # raw_diff_filter: 0=切（全部送る）/ 1=影（判定して記録するだけ・全部送る）/ 2=間引く
    raw_diff_filter: int = 2
    # det_frames_raw_fps: 0=生コマを送らない / 1〜29=その fps に間引く / 30 以上=来たコマ全部
    # Android は書かれていなければ 0（切）。Pi は生コマを送るための端末なので既定は全部
    raw_fps: int = 30
    # 撮影する時間帯。空なら常に撮る。例: ["10:55-21:15", "17:00-02:00"]
    windows: list[str] = field(default_factory=list)
    # 圧縮待ちの上限（枚）。超えた分はディスクへ逃がす
    queue_max: int = 32


@dataclass
class StorageConfig:
    root: str = "/var/lib/fservice-pi"
    # 空きがこれを切ったらディスクへ書かない（書けない分は捨てて数える）
    min_free_mb: int = 4096


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    camera: CameraConfig = field(default_factory=CameraConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)

    @property
    def spill_dir(self) -> Path:
        return Path(self.storage.root) / "spill"

    @property
    def outbox_dir(self) -> Path:
        return Path(self.storage.root) / "outbox"

    @property
    def failed_dir(self) -> Path:
        return Path(self.storage.root) / "failed"

    @property
    def token_path(self) -> Path:
        return Path(self.storage.root) / "token"


def _fill(obj, data: dict, section: str):
    for key, value in data.items():
        if not hasattr(obj, key):
            raise ValueError(f"設定の [{section}] に知らない項目があります: {key}")
        setattr(obj, key, value)


def load(path: str | Path) -> Config:
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    cfg = Config()
    for section in ("server", "camera", "capture", "storage"):
        if section in raw:
            _fill(getattr(cfg, section), raw.pop(section), section)
    if raw:
        raise ValueError(f"設定に知らない区画があります: {', '.join(raw)}")
    if cfg.capture.raw_diff_filter not in (0, 1, 2):
        raise ValueError("raw_diff_filter は 0 / 1 / 2 のどれか")
    return cfg
