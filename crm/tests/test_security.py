"""権限。**足りない人に見せない・書かせない。**"""
import pytest

from app.security import same_secret


class Test役割:
    def test_管理は管理者だけ(self, as_staff):
        assert as_staff.get("/admin").status_code == 403

    def test_管理者は入れる(self, as_owner):
        assert as_owner.get("/admin").status_code == 200

    def test_社外の方は書けない(self, as_outside, app, db):
        from app.extensions import db as _db
        from app.models import Partner
        with app.app_context():
            p = Partner(name="見本")
            _db.session.add(p)
            _db.session.commit()
            pid = p.id
        r = as_outside.patch(f"/api/partners/{pid}",
                           json={"field": "name", "value": "変えた"})
        assert r.status_code == 403

    def test_社外の方には何も出さない(self, as_outside):
        """**いまは器だけ。**何を見せるかが決まるまで出さない"""
        from app.extensions import db as _db
        from app.models import Partner
        r = as_outside.get("/partners")
        assert r.status_code == 200
        assert "1件も" in r.get_data(as_text=True) or "ありません" in r.get_data(as_text=True)


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
        from app.models import Partner
        from app.security import scope_query
        with app.test_request_context():
            _db.session.add(Partner(name="見本"))
            _db.session.commit()
            assert scope_query(_db.session.query(Partner), Partner).count() == 0
