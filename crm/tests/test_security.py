"""権限。**足りない人に見せない・書かせない。**"""
import pytest

from app.security import same_secret


class Test役割:
    def test_管理は管理者だけ(self, as_staff):
        assert as_staff.get("/admin").status_code == 403

    def test_管理者は入れる(self, as_owner):
        assert as_owner.get("/admin").status_code == 200

    def test_スタッフはゲストを直せる(self, as_staff, app, db):
        from app.extensions import db as _db
        from app.models import Guest
        with app.app_context():
            x = Guest(name="見本")
            _db.session.add(x)
            _db.session.commit()
            gid = x.id
        r = as_staff.patch(f"/api/guests/{gid}",
                           json={"field": "name", "value": "変えた"})
        assert r.status_code == 200


class Test知らない役割:
    """**消した役割が残っていても漏れない。**

    ひな形にあった `outside`（社外の方）は外した。古い利用者が残っていたときに、
    スタッフとして全件見える、を起こさない。
    """

    @pytest.fixture()
    def as_gone(self, app, client):
        from tests.conftest import _login
        _login(app, client, "outside")
        return client

    def _guest(self, app):
        from app.extensions import db as _db
        from app.models import Guest
        with app.app_context():
            x = Guest(name="見えてはいけない")
            _db.session.add(x)
            _db.session.commit()
            return x.id

    def test_一覧に1件も出さない(self, as_gone, app):
        self._guest(app)
        assert "見えてはいけない" not in as_gone.get("/guests").get_data(as_text=True)

    def test_詳細も開けない(self, as_gone, app):
        gid = self._guest(app)
        assert as_gone.get(f"/guests/{gid}").status_code == 404

    def test_書けない(self, as_gone, app):
        gid = self._guest(app)
        r = as_gone.patch(f"/api/guests/{gid}",
                          json={"field": "name", "value": "変えた"})
        assert r.status_code == 403


class Test秘密の比較:
    def test_合う(self, app):
        assert same_secret("abc", "abc") is True

    def test_違う(self, app):
        assert same_secret("abc", "abd") is False

    def test_空は通さない(self, app):
        assert same_secret("", "") is False

    def test_非ASCIIでも落ちない(self, app):
        """**`hmac.compare_digest` は文字列だと非ASCIIで例外を投げる。**
        バイト列にしてから比べる"""
        assert same_secret("鍵かぎ", "鍵かぎ") is True
        assert same_secret("鍵かぎ", "鍵かぎ2") is False


class Testスコープ:
    def test_未ログインには1件も返さない(self, app, db):
        from app.extensions import db as _db
        from app.models import Guest
        from app.security import scope_query
        with app.test_request_context():
            _db.session.add(Guest(name="見本"))
            _db.session.commit()
            assert scope_query(_db.session.query(Guest), Guest).count() == 0


class Testマスターを作らせない:
    """**選択肢に出していなくても、フォームを書き換えれば送れる。**

    ひな形では管理者がマスターを招待・昇格できてしまっていた。
    """

    def test_招待でマスターにならない(self, as_owner, app):
        from app.models import User
        as_owner.post("/admin/users", data={"email": "x@test", "name": "x",
                                            "role": "master"})
        with app.app_context():
            assert User.query.filter_by(email="x@test").one().role == "staff"

    def test_役割の変更でマスターにならない(self, as_owner, app):
        from app.extensions import db as _db
        from app.models import User
        with app.app_context():
            u = User(email="y@test", name="y", role="staff")
            _db.session.add(u)
            _db.session.commit()
            uid = u.id
        r = as_owner.post(f"/admin/users/{uid}", data={"action": "role",
                                                       "role": "master"})
        assert r.status_code == 403
        with app.app_context():
            assert _db.session.get(User, uid).role == "staff"
