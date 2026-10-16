"""**足りない表を作る。**既存の表と中身には触らない。

    python scripts/upgrade_db.py            # 何が足りないかを出すだけ
    python scripts/upgrade_db.py --apply    # 実際に作る

**できること・できないことをはっきりさせておく。**

    できる    新しい表を作る
    できない  **既存の表に列を足す・型を変える・列を消す**

SQLAlchemy の `create_all` は、無い表を作るだけで既存の表を変更しない。
列を足したくなったら、この仕組みでは足りない。**そのときは手順を別に作る。**
黙って動いたつもりになるのが一番まずいので、ここに書いておく。

列を足す必要が出たときの手順（SQLite）：

    1. `scripts/backup.py` で控えを取る
    2. `ALTER TABLE ... ADD COLUMN ...` を書いた移行スクリプトを作る
    3. **空回しで件数を確認してから**流す（土台 §6-7）
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect                                  # noqa: E402

from app import create_app                                      # noqa: E402
from app.extensions import db                                   # noqa: E402
import app.models                                               # noqa: E402,F401


def main() -> None:
    ap = argparse.ArgumentParser()
    # **--dry-run を既定にする**（土台 §4.5）
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    app = create_app()
    with app.app_context():
        have = set(inspect(db.engine).get_table_names())
        want = set(db.metadata.tables)
        missing = sorted(want - have)
        extra = sorted(have - want)

        print(f"定義されている表 {len(want)}／DBにある表 {len(have)}")
        print("-" * 56)
        for t in sorted(want):
            print(f"  {'あり  ' if t in have else '足りない'} {t}")
        if extra:
            print("\n定義に無い表（**消しません**。手で確認してください）:")
            for t in extra:
                print(f"  {t}")
        print("-" * 56)

        if not missing:
            print("足りない表はありません。")
            return
        if not a.apply:
            print(f"{len(missing)}件の表が足りません: {', '.join(missing)}")
            print("空回しです。作るには --apply を付けてください。")
            return

        db.create_all()
        now = set(inspect(db.engine).get_table_names())
        made = sorted(now - have)
        print(f"作りました: {', '.join(made) if made else 'なし'}")
        # **既存の表の列は変わっていない。**そこを誤解させない
        print("\n※ 既存の表の列は変更していません（この仕組みではできません）。")


if __name__ == "__main__":
    main()
