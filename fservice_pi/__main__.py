"""起動口。

  python -m fservice_pi run   --config /etc/fservice-pi/config.toml
  python -m fservice_pi bench --config /etc/fservice-pi/config.toml [--seconds 20]
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
    p.add_argument("command", choices=["run", "bench", "version"])
    p.add_argument("--config", default="/etc/fservice-pi/config.toml")
    p.add_argument("--seconds", type=float, default=20.0)
    p.add_argument("--width", type=int)
    p.add_argument("--height", type=int)
    p.add_argument("--fps", type=int)
    p.add_argument("--fourcc")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(threadName)s %(message)s")
    if args.command == "version":
        print(VERSION)
        return 0

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
