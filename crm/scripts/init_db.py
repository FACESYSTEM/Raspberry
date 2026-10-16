"""表を作り、最初の管理者を招待する。**1回だけ。**

    python scripts/init_db.py --email you@example.com --name 名前

パスワードはここで決めない。**招待リンクを出す**ので、それを開いて本人が決める。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app                                    # noqa: E402
from app.extensions import db                                 # noqa: E402
from app.models import OrgProfile, User                       # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--email", required=True)
    ap.add_argument("--name", required=True)
    a = ap.parse_args()

    app = create_app()
    with app.app_context():
        db.create_all()
        print("表を作りました。")
        OrgProfile.get()

        u = User.query.filter_by(email=a.email.lower()).first()
        if u is None:
            u = User(email=a.email.lower(), name=a.name, role="owner")
            db.session.add(u)
            db.session.commit()
            print(f"管理者を作りました：{u.email}")
        token = u.issue_invite(app.config["INVITE_TTL_HOURS"])
        db.session.commit()
        print("\n下記を開いてパスワードを決めてください。")
        print(f"  {app.config['BASE_URL']}/invite/{token}\n")


if __name__ == "__main__":
    main()
