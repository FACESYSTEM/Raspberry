"""起動口。

  python -m fservice_pi run   --config /etc/fservice-pi/config.toml
  python -m fservice_pi bench --config /etc/fservice-pi/config.toml [--seconds 20]
  python -m fservice_pi wifi  [--wifi-file /boot/firmware/fservice-wifi.txt]   # 起動時に root で
  python -m fservice_pi hub   [--port 8650]          # 個室の小型カメラからの受け口
  python -m fservice_pi rooms [--rooms-conf /etc/fservice-pi/rooms]   # 個室の端末をまとめて動かす
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys

from . import VERSION
from .config import load


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fservice_pi")
    p.add_argument("command", choices=["run", "bench", "version", "wifi", "hub", "rooms"])
    p.add_argument("--config", default="/etc/fservice-pi/config.toml")
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--fps", type=int)
    p.add_argument("--fourcc")
    p.add_argument("--wifi-file")
    p.add_argument("--port", type=int, default=8650)
    p.add_argument("--rooms-dir", default="/var/lib/fservice-pi/rooms")
    p.add_argument("--rooms-conf", default="/etc/fservice-pi/rooms")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(threadName)s %(message)s")
    if args.command == "version":
        print(VERSION)
        return 0
    if args.command == "wifi":
        from . import wifi
        return wifi.main(args.wifi_file or wifi.DEFAULT_PATH)
    if args.command == "hub":
        from . import hub
        return hub.main(args.port, args.rooms_dir)
    if args.command == "rooms":
        import threading
        from . import rooms
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        return rooms.run(args.rooms_conf, stop)

    cfg = load(args.config)
    for key in ("width", "height", "fps", "fourcc"):
        if getattr(args, key) is not None:
            setattr(cfg.camera, key, getattr(args, key))

    if args.command == "bench":
        from .bench import bench
        bench(cfg, args.seconds)
        return 0

    from .app import App
    app = App(cfg)
    signal.signal(signal.SIGTERM, lambda *_: app.request_stop())
    app.run_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
