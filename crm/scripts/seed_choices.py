"""選択肢の初期値。**足りなければ管理画面から足せる。**

    python scripts/seed_choices.py            # 何が入るか
    python scripts/seed_choices.py --apply

主担当・顧問種別は、いただいた資料に実在した値だけを入れる。
**こちらで勝手に増やさない**（社内に無い言葉を持ち込まない／規約 §7）。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app                    # noqa: E402
from app.extensions import db                 # noqa: E402
from app.models import Choice                 # noqa: E402

INIT = {
    "owner": ["〈担当A〉", "〈担当B〉", "〈担当C〉", "〈担当D〉"],
    # ヒアリング②Q3で「OEM加盟料＋DX利用料」と回答があったため DX利用料 を追加
    "service_kind": ["経営顧問", "FC", "OEM", "DX利用料", "社保/法人税 削減スキーム"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    app = create_app()
    with app.app_context():
        add = []
        for group, labels in INIT.items():
            for i, label in enumerate(labels):
                if Choice.query.filter_by(group=group, label=label).first():
                    continue
                add.append(Choice(group=group, label=label, sort_order=i * 10))
        for c in add:
            print(f"  足す: {c.group} / {c.label}")
        if not add:
            print("足すものはありません。")
            return
        if a.apply:
            db.session.add_all(add)
            db.session.commit()
            print(f"{len(add)}件足しました。")
        else:
            print("空回しです。入れるには --apply を付けてください。")


if __name__ == "__main__":
    main()
