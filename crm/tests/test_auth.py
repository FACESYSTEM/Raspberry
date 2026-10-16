"""ログインと招待。**壊れたら誰も入れない/誰でも入れる**ところ。"""
from datetime import timedelta

from app.timeutil import utcnow

from app.extensions import db as _db
from app.models import User


class Testログイン:
    def test_未ログインは追い返す(self, client):
        r = client.get("/", follow_redirects=False)
        assert r.status_code in (302, 401)

    def test_合えば入れる(self, as_owner):
        assert as_owner.get("/").status_code == 200

    def test_違えば入れない(self, app, client, db):
        with app.app_context():
            u = User(email="a@test", name="a", role="staff")
            u.set_password("correct-horse-battery")
            _db.session.add(u)
            _db.session.commit()
        r = client.post("/login", data={"email": "a@test", "password": "違う"},
                        follow_redirects=True)
        assert "違います" in r.get_data(as_text=True)

    def test_どちらが違うか教えない(self, client, db):
        """**利用者の存在を知られない。**総当たりの的を絞らせない"""
        r = client.post("/login", data={"email": "いない@test", "password": "x"},
                        follow_redirects=True)
        body = r.get_data(as_text=True)
        assert "メールアドレスかパスワードが違います" in body

    def test_無効にした人は入れない(self, app, client, db):
        with app.app_context():
            u = User(email="b@test", name="b", role="staff",
                     is_active_user=False)
            u.set_password("correct-horse-battery")
            _db.session.add(u)
            _db.session.commit()
        client.post("/login", data={"email": "b@test",
                                    "password": "correct-horse-battery"})
        assert client.get("/", follow_redirects=False).status_code in (302, 401)

    def test_平文を持たない(self, app, db):
        with app.app_context():
            u = User(email="c@test", name="c")
            u.set_password("とても秘密なパスワード")
            assert "とても秘密" not in (u.password_hash or "")


class Test招待:
    def _invited(self, app):
        with app.app_context():
            u = User(email="d@test", name="d", role="staff")
            _db.session.add(u)
            _db.session.commit()
            token = u.issue_invite(72)
            _db.session.commit()
            return token

    def test_リンクから決められる(self, app, client, db):
        token = self._invited(app)
        r = client.post(f"/invite/{token}",
                        data={"password": "twelve-chars-ok", "password2": "twelve-chars-ok"},
                        follow_redirects=True)
        assert r.status_code == 200
        with app.app_context():
            u = User.query.filter_by(email="d@test").one()
            assert u.check_password("twelve-chars-ok")

    def test_使ったら合言葉が消える(self, app, client, db):
        """**使い捨てにする。**残すと同じリンクで何度でも変えられる"""
        token = self._invited(app)
        client.post(f"/invite/{token}",
                    data={"password": "twelve-chars-ok", "password2": "twelve-chars-ok"},
                    follow_redirects=True)
        with app.app_context():
            assert User.query.filter_by(email="d@test").one().invite_token is None
        assert client.get(f"/invite/{token}").status_code == 410

    def test_期限切れは通さない(self, app, client, db):
        token = self._invited(app)
        with app.app_context():
            u = User.query.filter_by(email="d@test").one()
            u.invite_expires_at = utcnow() - timedelta(seconds=1)
            _db.session.commit()
        assert client.get(f"/invite/{token}").status_code == 410

    def test_短いパスワードは断る(self, app, client, db):
        token = self._invited(app)
        r = client.post(f"/invite/{token}", data={"password": "short",
                                                  "password2": "short"},
                        follow_redirects=True)
        assert "12文字以上" in r.get_data(as_text=True)

    def test_2つの欄が違えば断る(self, app, client, db):
        token = self._invited(app)
        r = client.post(f"/invite/{token}", data={"password": "twelve-chars-ok",
                                                  "password2": "twelve-chars-no"},
                        follow_redirects=True)
        assert "一致しません" in r.get_data(as_text=True)


class Test行き止まりを作らない:
    """**「使えません」で終わらせない。**次の一手が分かるようにする。

    実際に起きた：設定を済ませた方が招待リンクを再び開き、
    「管理者に再発行を依頼してください」だけが出て止まった。
    """

    def test_使い終わった招待でログインへ案内する(self, client, app):
        from app.extensions import db as _db
        from app.models import User
        with app.app_context():
            u = User(email="done@test", name="済", role="owner")
            token = u.issue_invite(24)
            _db.session.add(u)
            _db.session.commit()
        # パスワードを決める＝この合言葉は消える
        client.post(f"/invite/{token}",
                    data={"password": "passphrase-1234", "password2": "passphrase-1234"})
        r = client.get(f"/invite/{token}")
        assert r.status_code == 410
        body = r.get_data(as_text=True)
        assert "ログイン画面へ" in body
        assert "一度パスワードを設定すると無効になります" in body

    def test_知らない合言葉でも同じ画面(self, client):
        """**使用済みと間違いを見分けられない。**どちらにも効く書き方にする"""
        r = client.get("/invite/でたらめな合言葉")
        assert r.status_code == 410
        assert "ログイン画面へ" in r.get_data(as_text=True)


class Testメールが出ない環境:
    """**届かないものを待たせない。**"""

    def test_送れないことを先に伝える(self, client, app):
        app.config["SMTP_HOST"] = ""
        body = client.get("/forgot").get_data(as_text=True)
        assert "メールの自動送信を設定していません" in body
        assert "再設定のリンクを送る" not in body      # 押せるものを出さない

    def test_送れるなら今までどおり(self, client, app):
        app.config["SMTP_HOST"] = "smtp.example.test"
        body = client.get("/forgot").get_data(as_text=True)
        assert "再設定のリンクを送る" in body

    @staticmethod
    def _no_network(monkeypatch):
        """**試験で外に出ない。**偽のホストに繋ぎにいくと、
        繋がる・繋がらないで結果が揺れる（実際に落ちた）。
        """
        import app.auth as auth_mod
        monkeypatch.setattr(auth_mod, "send", lambda *a, **k: True)

    def test_送れないのに送りましたと言わない(self, client, app):
        app.config["SMTP_HOST"] = ""
        body = client.post("/forgot", data={"email": "x@test"}).get_data(as_text=True)
        assert "送りました" not in body

    def test_相手が居るかは出さない(self, client, app, as_owner, monkeypatch):
        """**登録の有無を外から調べられないようにする**（従来どおり）。

        画面に出るのは**打ち込んだ住所そのもの**なので、そこだけ伏せて比べる。
        """
        app.config["SMTP_HOST"] = "smtp.example.test"
        self._no_network(monkeypatch)

        def seen(email):
            got = client.post("/forgot", data={"email": email}).get_data(as_text=True)
            return got.replace(email, "＿")
        assert seen("nobody@test") == seen("owner@test")
