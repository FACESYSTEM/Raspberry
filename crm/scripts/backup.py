"""DBを控える。**世代を残す。**cronで毎日回す。

    python scripts/backup.py --dir /var/backups/app --keep 14

SQLiteは**コピーではなくバックアップAPIを使う**。書き込み中にコピーすると
壊れたファイルができる。
"""
import argparse
import gzip
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app                                    # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="backups")
    ap.add_argument("--keep", type=int, default=14)
    a = ap.parse_args()

    app = create_app()
    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if not uri.startswith("sqlite:///"):
        sys.exit("SQLite以外は、そのDBの手順で控えてください。")
    src = Path(uri[len("sqlite:///"):])
    if not src.exists():
        sys.exit(f"DBがありません: {src}")

    out = Path(a.dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    tmp = out / f"app-{stamp}.db"

    # **整合の取れた状態で取る**
    with sqlite3.connect(src) as s, sqlite3.connect(tmp) as d:
        s.backup(d)
    with open(tmp, "rb") as f, gzip.open(f"{tmp}.gz", "wb") as g:
        shutil.copyfileobj(f, g)
    tmp.unlink()
    print(f"控えました: {tmp}.gz")

    olds = sorted(out.glob("app-*.db.gz"))
    for p in olds[:-a.keep]:
        p.unlink()
        print(f"古いものを消しました: {p.name}")


if __name__ == "__main__":
    main()
