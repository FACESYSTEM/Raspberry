"""秘密の持ち方。**平文で置かない**ことを確かめる。"""
import pytest

from app import secretbox
from app.extensions import db as _db
from app.models import ApiKey, Credential


@pytest.fixture()
def keyed(app):
    from cryptography.fernet import Fernet
    app.config["CREDENTIAL_KEY"] = Fernet.generate_key().decode()
    yield app
    app.config["CREDENTIAL_KEY"] = ""


class Test暗号化:
    def test_平文をDBに置かない(self, keyed, as_owner, db):
        as_owner.post("/admin/credentials",
                      data={"name": "本番", "password": "とても秘密"},
                      follow_redirects=True)
        row = Credential.query.one()
        assert row.password_enc and "とても秘密" not in row.password_enc

    def test_戻せる(self, keyed, as_owner, db):
        as_owner.post("/admin/credentials",
                      data={"name": "本番", "password": "とりだせる"},
                      follow_redirects=True)
        with keyed.app_context():
            assert secretbox.open_(Credential.query.one().password_enc) == "とりだせる"

    def test_鍵が無ければ保存しない(self, app, as_owner, db):
        """**平文で置くくらいなら保存に失敗したほうがよい**"""
        app.config["CREDENTIAL_KEY"] = ""
        r = as_owner.post("/admin/credentials",
                          data={"name": "本番", "password": "ひみつ"},
                          follow_redirects=True)
        assert Credential.query.count() == 0
        assert "CREDENTIAL_KEY" in r.get_data(as_text=True)

    def test_一覧に平文を出さない(self, keyed, as_owner, db):
        as_owner.post("/admin/credentials",
                      data={"name": "本番", "password": "みえたらだめ"},
                      follow_redirects=True)
        body = as_owner.get("/admin/credentials").get_data(as_text=True)
        assert "みえたらだめ" not in body and "••••••••" in body

    def test_押した1件だけ返す(self, keyed, as_owner, db):
        as_owner.post("/admin/credentials",
                      data={"name": "本番", "password": "とりだせる"},
                      follow_redirects=True)
        cid = Credential.query.one().id
        assert as_owner.post(f"/admin/credentials/{cid}/reveal") \
            .get_json()["value"] == "とりだせる"

    def test_履歴に値を残さない(self, keyed, as_owner, db):
        from app.models import ChangeHistory
        as_owner.post("/admin/credentials",
                      data={"name": "本番", "password": "のこったらだめ"},
                      follow_redirects=True)
        for h in ChangeHistory.query.all():
            assert "のこったらだめ" not in (h.old_value or "") + (h.new_value or "")

    def test_空なら変えない(self, keyed, as_owner, db):
        """**毎回打たせるとかえって危ない。**空欄は「変えない」の意味"""
        as_owner.post("/admin/credentials",
                      data={"name": "本番", "password": "もとのまま"},
                      follow_redirects=True)
        row = Credential.query.one()
        as_owner.post("/admin/credentials",
                      data={"id": row.id, "name": "本番", "login_id": "admin",
                            "password": ""}, follow_redirects=True)
        row = Credential.query.one()
        with keyed.app_context():
            assert secretbox.open_(row.password_enc) == "もとのまま"
        assert row.login_id == "admin"


class Test外部に渡す鍵:
    def test_平文を保存しない(self, as_owner, db):
        r = as_owner.post("/admin/apikeys", data={"name": "サイト"},
                          follow_redirects=True)
        body = r.get_data(as_text=True)
        row = ApiKey.query.one()
        # 発行直後は画面に出るが、DBにはハッシュしか無い
        assert row.key_hash and row.prefix in body
        assert row.key_hash not in body

    def test_1度しか見られない(self, as_owner, db):
        as_owner.post("/admin/apikeys", data={"name": "サイト"},
                      follow_redirects=True)
        body = as_owner.get("/admin/apikeys").get_data(as_text=True)
        assert "発行しました" not in body

    def test_照合できる(self, app, as_owner, db):
        from app.admin import verify_api_key
        r = as_owner.post("/admin/apikeys", data={"name": "サイト"},
                          follow_redirects=True)
        body = r.get_data(as_text=True)
        row = ApiKey.query.one()
        import re
        m = re.search(rf'({row.prefix}_[A-Za-z0-9_\-]+)', body)
        assert m
        with app.test_request_context():
            assert verify_api_key(m.group(1)) is not None
            assert verify_api_key("よその鍵") is None

    def test_失効させたら通さない(self, app, as_owner, db):
        from app.admin import verify_api_key
        import re
        r = as_owner.post("/admin/apikeys", data={"name": "サイト"},
                          follow_redirects=True)
        row = ApiKey.query.one()
        m = re.search(rf'({row.prefix}_[A-Za-z0-9_\-]+)', r.get_data(as_text=True))
        as_owner.post("/admin/apikeys",
                      data={"action": "revoke", "id": row.id},
                      follow_redirects=True)
        with app.test_request_context():
            assert verify_api_key(m.group(1)) is None
