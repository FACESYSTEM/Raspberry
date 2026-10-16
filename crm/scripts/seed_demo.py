"""見本のデータ。**実在の相手を入れない**（設計指示書 §0.3）。

    python scripts/seed_demo.py            # 何が入るか
    python scripts/seed_demo.py --apply

画面の見え方を確かめるためのものです。**架空のゲスト・キャスト**だけを入れます。
実データ（Notion の書き出し）を入れる前に、必ず空回しで件数を確かめてください。
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from app.models import Assignment, Bottle, Cast, Corp, Guest, Visit  # noqa: E402

# 番号, 名前
CASTS = [(1, "見本あい"), (2, "見本かな"), (None, "見本さき")]

# **わざと汚しておく。**きれいなデータしか入っていないと、
# 「要確認」の仕組みが効いているか確かめられない
#   番号, 名前, フリガナ, 法人, 担当（キャスト番号の位置, レベル）, ボトル, 誕生月日, 何日前に来店
GUESTS = [
    (9001, "架空 一郎", "カクウ イチロウ", "見本商事株式会社", (0, "メイン"),
     ["マッカラン⑩P"], (date.today().month, 3), [2, 30]),
    (9002, "仮名 二郎", "カメイ ジロウ", "見本商事株式会社", (1, "A1"),
     ["知多⑯P", "吉四六P27"], None, [10]),
    (9002, "試作 三郎", "シサク サブロウ", "", None,              # 番号の重複
     [], (1, None), []),                                        # 日が分からない
    (None, "例示 四郎", "", "架空工業㈱", (2, None),              # 番号なし
     ["現物裏2段目シーバス"], None, []),
    (9005, "", "", "", None, [], None, []),                      # 名前なし・担当が分からない
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    app = create_app()
    with app.app_context():
        if Guest.query.filter(Guest.guest_no.between(9001, 9005)).count():
            print("見本は既に入っています。")
            return 0
        print(f"  入れる: キャスト {len(CASTS)}人・ゲスト {len(GUESTS)}件（すべて架空）")
        if not a.apply:
            print("-" * 60)
            print("空回しです。入れるには --apply を付けてください。")
            return 0
        casts = [Cast(cast_no=no, name=name) for no, name in CASTS]
        db.session.add_all(casts)
        corps = {}
        for no, name, kana, corp, who, bottles, birth, visits in GUESTS:
            x = Guest(guest_no=no, name=name or None, kana=kana or None)
            if corp:
                x.corp = corps.setdefault(corp, Corp(name=corp))
            if who:
                db.session.add(Assignment(guest=x, cast=casts[who[0]],
                                          level=who[1]))
            for b in bottles:
                db.session.add(Bottle(guest=x, label=b))
            if birth:
                x.birth_month, x.birth_day = birth
            for d in visits:
                db.session.add(Visit(guest=x,
                                     visited_on=date.today() - timedelta(days=d)))
            db.session.add(x)
        db.session.commit()
        print("入れました。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
