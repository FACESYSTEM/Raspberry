"""選択肢の初期値。**足りなければ管理画面から足せる。**

    python scripts/seed_choices.py            # 何が入るか
    python scripts/seed_choices.py --apply

担当レベルは、Notion の書き出しに実在した値だけを入れる（規約 §7.3）。
**こちらで勝手に増やさない・意味を推測して名前を変えない**（店に無い言葉を持ち込まない）。
`メインA2`（2件）は2つの値の組み合わせなので選択肢にしない。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app                    # noqa: E402
from app.extensions import db                 # noqa: E402
from app.models import Choice                 # noqa: E402

# Notion の件数順（メイン161・完全159・A1 43・A2 37・A 22・B 1）
INIT = {
    "level": ["メイン", "完全", "A1", "A2", "A", "B"],
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
