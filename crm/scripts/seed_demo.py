"""見本のデータ。**実在の相手を入れない**（設計指示書 §0.3）。

    python scripts/seed_demo.py            # 何が入るか
    python scripts/seed_demo.py --apply

画面の見え方を確かめるためのものです。**架空の会社**だけを入れます。
実データを入れる前に、必ず空回しで件数を確かめてください（§6-7）。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app                    # noqa: E402
from app.extensions import db                 # noqa: E402
from app.models import Partner                # noqa: E402

# **わざと汚しておく。**きれいなデータしか入っていないと、
# 「要確認」の仕組みが効いているか確かめられない
#   名前, 区分, 担当者, 電話, 整理番号
# **わざと欠けさせてある。**「要確認」が効くことを、入れた直後に確かめられる
ROWS = [
    ("見本商事株式会社",     "仕入先", "見本 太郎", "03-0000-0001", "P-001"),
    ("架空工業株式会社",     "得意先", "架空 花子", "",            "P-002"),
    ("試作サービス合同会社",  "",      "",         "03-0000-0003", ""),
    ("仮称デザイン事務所",    "外注",   "仮名 次郎", "",            ""),
    ("例示ホールディングス",  "",      "",         "",            "P-002"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    app = create_app()
    with app.app_context():
        have = {p.name for p in Partner.query.all()}
        add = [r for r in ROWS if r[0] not in have]
        for name, kind, contact, tel, code in add:
            print("  入れる: %s" % name)
            if a.apply:
                # **整理番号は重複も未採番も許す。**実データがそうなっている
                db.session.add(Partner(name=name, kind=kind or None,
                                       contact_name=contact or None,
                                       tel=tel or None, code=code or None))
        if a.apply:
            db.session.commit()
            print("入れました: %d件" % len(add))
        else:
            print("-" * 60)
            print("空回しです。入れるには --apply を付けてください。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
