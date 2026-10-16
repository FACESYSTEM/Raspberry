"""**この部品だけで動くこと。**土台の作りに寄りかかっていないかを確かめる。

Flask-Login も Flask-WTF も使わない、最小の土台に載せて試す。
ここが通らなければ、他のシステムへ持っていっても動かない。
"""
import sys
import warnings
from pathlib import Path

import pytest
from flask import Flask, render_template_string
from flask_sqlalchemy import SQLAlchemy

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from fixmode import FixMode                                   # noqa: E402

BASE = """<!doctype html><html><body>
{% include "fixmode/widget.html" %}
{% block content %}{% endblock %}
{% include "fixmode/switch.html" %}
{% include "fixmode/script.html" %}
</body></html>"""


@pytest.fixture()
def env():
    """役割を切り替えられる、最小の土台。"""
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite://", SECRET_KEY="t",
                      TESTING=True)
    db = SQLAlchemy(app)
    role = {"v": "user"}

    fix = FixMode(db).init_app(
        app,
        can_comment=lambda: role["v"] in ("user", "dev", "writer"),
        can_fix=lambda: role["v"] == "dev",
        can_close=lambda: role["v"] == "user",
        who=lambda: (1, "見本 太郎"))

    @app.route("/page")
    def page():
        return render_template_string(BASE)

    with app.app_context():
        db.create_all()
    return app, app.test_client(), role, fix.Note, db


class Test土台に寄りかかっていない:
    def test_ログインの仕掛けが無くても動く(self, env):
        _app, c, _role, _N, _db = env
        assert c.get("/page").status_code == 200

    def test_CSRFの仕掛けが無くても書ける(self, env):
        _app, c, _role, N, _db = env
        r = c.post("/notes", json={"body": "ここ", "page": "/page"})
        assert r.status_code == 200 and r.get_json()["ok"] is True

    def test_一覧が出る(self, env):
        _app, c, _role, _N, _db = env
        c.post("/notes", json={"body": "ここ", "page": "/page"})
        assert "ここ" in c.get("/notes").get_data(as_text=True)

    def test_見た目と動きを自分で持っている(self, env):
        _app, c, _role, _N, _db = env
        body = c.get("/page").get_data(as_text=True)
        assert "fixmode.css" in body and "fixmode.js" in body


class Test直す人と閉じる人:
    def _one(self, app, c, N):
        c.post("/notes", json={"body": "ここ", "page": "/page"})
        with app.app_context():
            return N.query.one().id

    def test_使い手は直したと言えない(self, env):
        app, c, role, N, _db = env
        nid = self._one(app, c, N)
        role["v"] = "user"
        assert c.post(f"/notes/{nid}/fixed").status_code == 403

    def test_作り手は閉じられない(self, env):
        """**直したつもりが直っていない、を通さない。**"""
        app, c, role, N, _db = env
        nid = self._one(app, c, N)
        role["v"] = "dev"
        assert c.post(f"/notes/{nid}/accept").status_code == 403

    def test_直すと確認待ちになる(self, env):
        app, c, role, N, db = env
        nid = self._one(app, c, N)
        role["v"] = "dev"
        c.post(f"/notes/{nid}/fixed", json={"fixed_note": "直した"})
        with app.app_context():
            n = db.session.get(N, nid)
            assert n.state == "fixed" and n.done_at is None
            assert n.fixed_by_name == "見本 太郎"

    def test_閉じられる(self, env):
        app, c, role, N, db = env
        nid = self._one(app, c, N)
        role["v"] = "dev"
        c.post(f"/notes/{nid}/fixed")
        role["v"] = "user"
        c.post(f"/notes/{nid}/accept", json={})
        with app.app_context():
            assert db.session.get(N, nid).state == "done"

    def test_差し戻しても直した記録は残る(self, env):
        app, c, role, N, db = env
        nid = self._one(app, c, N)
        role["v"] = "dev"
        c.post(f"/notes/{nid}/fixed", json={"fixed_note": "直した"})
        role["v"] = "user"
        c.post(f"/notes/{nid}/reopen", json={"reopen_note": "まだ"})
        with app.app_context():
            n = db.session.get(N, nid)
            assert n.state == "open" and n.reopen_note == "まだ"
            assert n.fixed_at is not None


class Test画面への出し方:
    def test_その画面のものだけ渡す(self, env):
        _app, c, _role, _N, _db = env
        c.post("/notes", json={"body": "こっち", "page": "/page"})
        c.post("/notes", json={"body": "よそ", "page": "/other"})
        body = c.get("/page").get_data(as_text=True)
        assert "こっち" in body and "よそ" not in body

    def test_閉じたものは出さない(self, env):
        app, c, role, N, _db = env
        c.post("/notes", json={"body": "こっち", "page": "/page"})
        with app.app_context():
            nid = N.query.one().id
        role["v"] = "dev"
        c.post(f"/notes/{nid}/fixed")
        role["v"] = "user"
        c.post(f"/notes/{nid}/accept", json={})
        assert "こっち" not in c.get("/page").get_data(as_text=True)

    def test_直しただけでは出し続ける(self, env):
        """**確認するには、その場所が要る。**"""
        app, c, role, N, _db = env
        c.post("/notes", json={"body": "こっち", "page": "/page"})
        with app.app_context():
            nid = N.query.one().id
        role["v"] = "dev"
        c.post(f"/notes/{nid}/fixed")
        assert "こっち" in c.get("/page").get_data(as_text=True)

    def test_書けない人には何も出さない(self, env):
        _app, c, role, _N, _db = env
        role["v"] = "guest"
        body = c.get("/page").get_data(as_text=True)
        assert "modeswitch" not in body and "fixmode.js" not in body


class Test開けない人に見せない:
    """**押して403になるのが一番こたえる。**出すなら開けるようにする。"""

    def test_書くだけの人に一覧のリンクを出さない(self, env):
        _app, c, role, _N, _db = env
        role["v"] = "writer"                # 書けるが、直しも閉じもできない
        body = c.get("/page").get_data(as_text=True)
        assert "modeswitch" in body         # 書けるので切り替えは出る
        assert "届いているもの" not in body

    def test_その人が押しても403になる経路を出していない(self, env):
        """出していないことと、通れないことの両方を確かめる。"""
        _app, c, role, _N, _db = env
        role["v"] = "writer"
        assert c.get("/notes").status_code == 403

    def test_閉じられる人にはリンクを出す(self, env):
        _app, c, role, _N, _db = env
        role["v"] = "user"
        assert "届いているもの" in c.get("/page").get_data(as_text=True)

    def test_全体の件数を書くだけの人に見せない(self, env):
        """**全画面ぶんの数。**見られない画面の数が見えるのはおかしい"""
        _app, c, role, _N, _db = env
        c.post("/notes", json={"body": "よそ", "page": "/other"})
        role["v"] = "writer"
        body = c.get("/page").get_data(as_text=True)
        assert '<span class="n">' not in body


class Test組み込み方の誤りを黙って通さない:
    """**この部品の主張がそこにある。**同じ人が両方できると意味が半分になる。"""

    def test_直す人と閉じる人が同じなら知らせる(self):
        app = Flask(__name__)
        app.config.update(SQLALCHEMY_DATABASE_URI="sqlite://", SECRET_KEY="t")
        db = SQLAlchemy(app)
        same = lambda: True                                   # noqa: E731
        with pytest.warns(UserWarning, match="can_fix と can_close"):
            FixMode(db).init_app(app, can_comment=same, can_fix=same,
                                 can_close=same, who=lambda: (1, "見本"))

    def test_別ならば何も言わない(self):
        app = Flask(__name__)
        app.config.update(SQLALCHEMY_DATABASE_URI="sqlite://", SECRET_KEY="t")
        db = SQLAlchemy(app)
        with warnings.catch_warnings():
            warnings.simplefilter("error")                    # 出たら落とす
            FixMode(db).init_app(app, can_comment=lambda: True,
                                 can_fix=lambda: True, can_close=lambda: False,
                                 who=lambda: (1, "見本"))


class Test表の使い回し:
    def test_別のDBには別の表を渡す(self):
        """**`id()` を鍵にしない。**番号が使い回されると取り違える"""
        made = []
        for _ in range(2):
            app = Flask(__name__)
            app.config.update(SQLALCHEMY_DATABASE_URI="sqlite://", SECRET_KEY="t")
            db = SQLAlchemy(app)
            made.append((db, FixMode(db).init_app(
                app, can_comment=lambda: True, can_fix=lambda: True,
                can_close=lambda: False, who=lambda: (1, "見本")).Note))
        assert made[0][1] is not made[1][1]

    def test_同じDBには同じ表を渡す(self):
        app = Flask(__name__)
        app.config.update(SQLALCHEMY_DATABASE_URI="sqlite://", SECRET_KEY="t")
        db = SQLAlchemy(app)
        a = FixMode(db).init_app(app, can_comment=lambda: True,
                                 can_fix=lambda: True, can_close=lambda: False,
                                 who=lambda: (1, "見本")).Note
        app2 = Flask(__name__)
        app2.config.update(SECRET_KEY="t")
        b = FixMode(db).init_app(app2, can_comment=lambda: True,
                                 can_fix=lambda: True, can_close=lambda: False,
                                 who=lambda: (1, "見本")).Note
        assert a is b


class Test名前が無いとき:
    """**古いデータには記録者が入っていないことがある。**

    実際に「が確認」とだけ出て、文が壊れて見えた。
    """

    def test_確認した人の名前が無くても文が壊れない(self, env):
        app, c, role, N, db = env
        c.post("/notes", json={"body": "ここ", "page": "/page"})
        with app.app_context():
            n = N.query.one()
            n.state = "done"
            n.done_by_name = None
            from datetime import datetime
            n.done_at = datetime(2026, 9, 1, 3, 9)
            db.session.commit()
        body = c.get("/notes").get_data(as_text=True)
        assert "が確認" not in body
        assert "確認済み" in body

    def test_名前があれば今までどおり(self, env):
        app, c, role, N, db = env
        c.post("/notes", json={"body": "ここ", "page": "/page"})
        with app.app_context():
            n = N.query.one()
            n.state = "done"
            n.done_by_name = "見本 太郎"
            db.session.commit()
        assert "見本 太郎が確認" in c.get("/notes").get_data(as_text=True)
