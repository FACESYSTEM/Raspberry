"""**足りない列を足す。**既存のデータには触らない。

    python scripts/add_columns.py            # 何が足りないかを出すだけ
    python scripts/add_columns.py --apply    # 実際に足す

`upgrade_db.py` は**表**を作るだけで、既存の表に**列**は足せない。
モデルに項目を増やしたあと、動いているサーバーに反映するのがこれ。

**できること・できないこと。**

    できる    列を足す（既定値つき）
    できない  列を消す・型を変える・名前を変える

SQLite は `ALTER TABLE ADD COLUMN` しか受け付けない。消す・変えるが必要になったら、
**表を作り直して移し替える**ことになるので、そのときは別に手順を作る。

**必ず控えを取ってから流す**（`scripts/backup.py`）。空回しで内容を確かめてから。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text                          # noqa: E402

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
import app.models                                             # noqa: E402,F401


def _sql_type(col) -> str:
    """SQLite に渡す型。**分からない型は文字列に落とす**（止めない）。"""
    try:
        return col.type.compile(dialect=db.engine.dialect)
    except Exception:                                          # noqa: BLE001
        return "TEXT"


def _default(col) -> str:
    """既定値の指定。**NOT NULL の列は既定値が無いと足せない。**"""
    d = col.default
    if d is not None and getattr(d, "is_scalar", False):
        v = d.arg
        if isinstance(v, bool):
            return f" NOT NULL DEFAULT {1 if v else 0}"
        if isinstance(v, (int, float)):
            return f" NOT NULL DEFAULT {v}"
        if isinstance(v, str):
            return " NOT NULL DEFAULT '{}'".format(v.replace("'", "''"))
    if not col.nullable:
        # **既定値が分からないのに NOT NULL は足せない。**空文字で受ける
        return " NOT NULL DEFAULT ''"
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    # **--dry-run を既定にする**（土台 §4.5）
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    app = create_app()
    with app.app_context():
        insp = inspect(db.engine)
        have_tables = set(insp.get_table_names())
        plan = []
        for name, table in db.metadata.tables.items():
            if name not in have_tables:
                print(f"  表ごとありません: {name}"
                      f"（先に scripts/upgrade_db.py を流してください）")
                continue
            have = {c["name"] for c in insp.get_columns(name)}
            for col in table.columns:
                if col.name in have:
                    continue
                sql = (f'ALTER TABLE "{name}" ADD COLUMN "{col.name}" '
                       f"{_sql_type(col)}{_default(col)}")
                plan.append((name, col.name, sql))

        if not plan:
            print("足りない列はありません。")
            return

        print(f"足りない列 {len(plan)}本")
        print("-" * 60)
        for tbl, colname, sql in plan:
            print(f"  {tbl}.{colname}")
            print(f"      {sql}")
        print("-" * 60)

        if not a.apply:
            print("空回しです。足すには --apply を付けてください。")
            print("**先に scripts/backup.py で控えを取ってください。**")
            return

        for tbl, colname, sql in plan:
            db.session.execute(text(sql))
        db.session.commit()

        insp = inspect(db.engine)
        ok = 0
        for tbl, colname, _ in plan:
            if colname in {c["name"] for c in insp.get_columns(tbl)}:
                ok += 1
        print(f"足しました: {ok}/{len(plan)}本")
        # **既存の行には既定値が入る。**中身は変えていない
        print("※ 既存の行には既定値が入ります。ほかのデータは変えていません。")


if __name__ == "__main__":
    main()
