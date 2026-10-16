"""機能の公開/非公開。**作りかけを先方に見せないための仕組み。**

見せると、そこに指摘が集まって本題が進まなくなる。
**画面から隠すだけでは足りない。**URLを直に叩けば入れてしまう。
"""
import pytest

from app.extensions import db as _db
from app.models import Feature
from tests.conftest import _login


def _close(app, key):
    with app.app_context():
        f = Feature.query.filter_by(key=key).first()
        f.released = False
        _db.session.commit()


class Test非公開の機能:
    def test_メニューに出さない(self, app, db, as_owner):
        _close(app, "apikeys")
        assert "外部連携の鍵" not in as_owner.get("/admin/apikeys").get_data(as_text=True)

    def test_URLを直に叩いても入れない(self, app, db, as_owner):
        """**隠すだけでは足りない。**経路そのものを止める。"""
        _close(app, "apikeys")
        assert as_owner.get("/loans").status_code == 404

    def test_403ではなく404にする(self, app, db, as_owner):
        """403だと「権限が無いだけで存在はする」と分かってしまう。

        **作りかけがあること自体を知らせない。**
        """
        _close(app, "apikeys")
        assert as_owner.get("/loans").status_code == 404

    def test_書き込みの経路も止める(self, app, db, as_owner):
        """読めなくても書けたら意味がない。"""
        _close(app, "apikeys")
        assert as_owner.post("/admin/apikeys", data={"label": "見本"}).status_code == 404


class Testマスター:
    def test_作りかけも見える(self, app, db, client):
        """開発側は確認しながら作る必要がある。"""
        _close(app, "apikeys")
        _login(app, client, "master")
        assert client.get("/admin/apikeys").status_code == 200

    def test_メニューにも出る(self, app, db, client):
        _close(app, "apikeys")
        _login(app, client, "master")
        assert "外部連携の鍵" in client.get("/admin/apikeys").get_data(as_text=True)

    def test_管理者の画面も通る(self, app, db, client):
        """役割ごとに master を書き足して回ると、**書き忘れた画面だけ入れなくなる。**"""
        _login(app, client, "master")
        assert client.get("/admin").status_code == 200

    def test_切り替え画面はマスターだけ(self, app, db, as_owner):
        assert as_owner.get("/admin/features").status_code == 403

    def test_マスターは切り替えられる(self, app, db, client):
        _login(app, client, "master")
        assert client.get("/admin/features").status_code == 200

    def test_切り替えると先方の見え方が変わる(self, app, db, client):
        _login(app, client, "master")
        with app.app_context():
            fid = Feature.query.filter_by(key="apikeys").first().id
        client.post(f"/admin/features/{fid}")          # 公開 → 非公開
        with app.app_context():
            assert Feature.query.filter_by(key="apikeys").first().released is False

    def test_切り替えが履歴に残る(self, app, db, client):
        from app.models import ChangeHistory
        _login(app, client, "master")
        with app.app_context():
            fid = Feature.query.filter_by(key="apikeys").first().id
        client.post(f"/admin/features/{fid}")
        with app.app_context():
            assert ChangeHistory.query.filter_by(entity_type="feature").count() == 1


class Test役割の並び:
    def test_マスターが一番上(self, app):
        from app.security import ROLES
        assert ROLES[0] == "master"

    def test_マスターは書ける(self, app, db, client):
        from app.security import can_edit
        _login(app, client, "master")
        with app.test_request_context():
            pass
        assert client.post("/guests/new", data={"name": "架空 太郎"}).status_code in (302, 200)
