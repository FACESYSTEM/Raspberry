"""個室の端末をまとめて動かす（fservice-rooms）。

/etc/fservice-pi/rooms/<部屋>.toml を全部読み、部屋ごとに撮影端末（App）を 1 つずつ、
1 つのプログラムの中で動かす（20 部屋でもメモリが足りるように、部屋ごとに別のプログラムは立てない）。
部屋を足したら（add-room.sh）このプログラムを再起動する。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from .app import App, sd_notify
from .config import load

log = logging.getLogger(__name__)

ROOMS_CONF = "/etc/fservice-pi/rooms"


def load_rooms(conf_dir: str | Path = ROOMS_CONF) -> list[App]:
    apps = []
    for path in sorted(Path(conf_dir).glob("*.toml")):
        try:
            cfg = load(path)
        except Exception as e:  # 1 部屋の設定の誤りで、ほかの部屋まで止めない
            log.error("部屋の設定を読めない %s: %s", path.name, e)
            continue
        if cfg.camera.kind != "inbox":
            log.error("部屋の設定 %s の camera.kind が inbox ではない（%s）。飛ばす", path.name, cfg.camera.kind)
            continue
        apps.append(App(cfg, label=path.stem))
    return apps


def run(conf_dir: str | Path = ROOMS_CONF, stop: threading.Event | None = None) -> int:
    stop = stop or threading.Event()
    apps = load_rooms(conf_dir)
    if not apps:
        log.info("部屋がまだない（%s）。add-room.sh で足すと動き出す", conf_dir)
    for app in apps:
        app.start()
    sd_notify("READY=1")
    log.info("個室の端末を %d 部屋動かした: %s", len(apps), ", ".join(a.label for a in apps))
    try:
        while not stop.is_set():
            stop.wait(1)
    except KeyboardInterrupt:
        pass
    finally:
        for app in apps:
            app.request_stop()
        for app in apps:
            app.shutdown()
    return 0
