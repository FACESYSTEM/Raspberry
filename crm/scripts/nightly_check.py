"""夜間の点検。**壊れていることに翌朝までに気づくため。**

    python scripts/nightly_check.py            # 画面に出す
    python scripts/nightly_check.py --mail     # 問題があればメールする

3つの決めごと。

  * **問題があるときだけ知らせる。**毎日届くと読まれなくなる
  * **業務の数字を載せない。**未処理件数や期限は人が決めることで、不具合ではない。
    混ぜると、本当に壊れた日の知らせが業務の話に埋もれる
  * **「5xxでなければOK」にしない。**4xxもNGにする。手前（WAF・プロキシ）で
    全部弾かれている状態を「OK」と出す点検は、無いのと同じ

**User-Agent を名乗る。**名乗らないと `Python-urllib/3.x` になり、WAFに
弾かれて点検だけが失敗する（直すものが無いのに毎晩知らせが来る）。
"""
import argparse
import sqlite3
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app                                    # noqa: E402
from app.mailer import send                                   # noqa: E402

UA = "app-nightly/1.0"
BACKUP_LIMIT_HOURS = 30
DISK_LIMIT_PCT = 90

lines, bad = [], 0


def say(ok: bool, label: str, detail: str = "") -> None:
    global bad
    if not ok:
        bad += 1
    lines.append(f"{'OK' if ok else 'NG'} {label}  {detail}".rstrip())


def check_db(uri: str) -> None:
    if not uri.startswith("sqlite:///"):
        say(True, "DBの整合性", "SQLite以外のため省略")
        return
    p = Path(uri[len("sqlite:///"):])
    if not p.exists():
        say(False, "DBの整合性", "ファイルがありません")
        return
    with sqlite3.connect(p) as c:
        got = c.execute("PRAGMA integrity_check").fetchone()[0]
    say(got == "ok", "DBの整合性", got)


def check_backup(dirname: str) -> None:
    d = Path(dirname)
    files = sorted(d.glob("app-*.db.gz")) if d.exists() else []
    if not files:
        say(False, "バックアップ", "1つもありません")
        return
    newest = files[-1]
    hours = round((datetime.now()
                   - datetime.fromtimestamp(newest.stat().st_mtime))
                  .total_seconds() / 3600, 1)
    say(hours <= BACKUP_LIMIT_HOURS, "バックアップ",
        f"{newest.name}／{hours}時間前／{len(files)}世代")


def check_keys(app) -> None:
    """秘密の鍵。**保管したものがあるのに読めない状態**だけをNGにする。

    鍵を使っていないだけならNGにしない。**毎晩NGが出続けると読まれなくなる**（§3.4）。
    「まだ1件も保管していない」は不具合ではない。
    """
    from app.models import Credential                          # noqa: PLC0415
    from app.secretbox import ready                            # noqa: PLC0415
    with app.app_context():
        stored = Credential.query.filter(
            Credential.password_enc.isnot(None)).count()
        ok = ready()
    if stored == 0:
        say(True, "秘密の保管鍵", "保管なし" if not ok else "設定あり")
        return
    say(ok, "秘密の保管鍵",
        f"{stored}件保管済み" if ok else f"{stored}件あるが CREDENTIAL_KEY が無く読めません")


def check_disk() -> None:
    import shutil
    t, u, _ = shutil.disk_usage("/")
    pct = round(u * 100 / t)
    say(pct < DISK_LIMIT_PCT, "ディスク", f"{pct}% 使用")


def check_pages(base: str, paths) -> None:
    """**4xxもNG。**手前で弾かれていることに気づけるように。"""
    for path in paths:
        try:
            req = urllib.request.Request(base + path, method="GET",
                                         headers={"User-Agent": UA})
            code = urllib.request.urlopen(req, timeout=15).getcode()
        except urllib.error.HTTPError as e:
            code = e.code
        except Exception as e:                       # noqa: BLE001
            say(False, f"画面 {path}", str(e)[:80])
            continue
        say(code < 400, f"画面 {path}", f"HTTP {code}"
            + ("（手前で弾かれています）" if code in (403, 429) else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mail", action="store_true")
    ap.add_argument("--backup-dir", default="backups")
    a = ap.parse_args()

    app = create_app()
    base = app.config["BASE_URL"]
    check_disk()
    check_db(app.config["SQLALCHEMY_DATABASE_URI"])
    check_keys(app)
    check_backup(a.backup_dir)
    check_pages(base, ("/login",))

    head = f"夜間点検  {datetime.now():%Y-%m-%d %H:%M}"
    body = "\n".join([head, "=" * 52, *lines, "=" * 52,
                      f"システムの問題 {bad}件" if bad else "システムの問題 なし"])
    print(body)

    # **問題が無ければ送らない**
    if a.mail and bad:
        with app.app_context():
            send(app.config.get("ALERT_TO", ""),
                 f"[点検] {bad}件の問題", body)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
