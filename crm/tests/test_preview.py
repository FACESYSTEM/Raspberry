"""見え方の確認モード。**確認したつもりが、確認になっていない**を防ぐ。

ここが緩むと「マスターだから見えていただけ」に気づけないまま公開してしまう。
"""
import pytest

from app.extensions import db as _db
from app.models import Feature, Guest, User


class Test切り替え:
    def test_マスター以外は切り替えられない(self, as_owner):
        assert as_owner.post("/preview/staff").status_code == 403

    def test_知らない役割は無視する(self, as_master, app):
        as_master.post("/preview/でたらめ")
        with as_master.session_transaction() as s:
            assert "preview_as" not in s

    def test_マスターは名乗れない(self, as_master):
        """**マスターとしての確認は要らない。**抜けられなくなるだけ"""
        as_master.post("/preview/master")
        with as_master.session_transaction() as s:
            assert s.get("preview_as") != "master"

    def test_いつでも戻れる(self, as_master):
        as_master.post("/preview/staff")
        as_master.get("/preview/off")
        assert as_master.get("/admin").status_code == 200


class Test役割ごとの見え方:
    def test_担当者では管理に入れない(self, as_master):
        as_master.post("/preview/staff")
        assert as_master.get("/admin").status_code == 403

    def test_管理者では管理に入れる(self, as_master):
        as_master.post("/preview/owner")
        assert as_master.get("/admin").status_code == 200

    def test_非公開の機能は404(self, as_master, app):
        """**確認モード中はマスター特権を外す。**外さないと確認にならない"""
        with app.app_context():
            row = Feature.query.filter_by(key="apikeys").first() or Feature(key="apikeys")
            row.released = False
            _db.session.add(row)
            _db.session.commit()
        assert as_master.get("/admin/apikeys").status_code == 200
        as_master.post("/preview/owner")
        assert as_master.get("/admin/apikeys").status_code == 404

    def test_スタッフではキャストを直せない(self, as_master, app):
        """**確認モード中は名乗っている役割で判定する。**"""
        from app.models import Cast
        with app.app_context():
            c = Cast(name="見本")
            _db.session.add(c)
            _db.session.commit()
            cid = c.id
        as_master.post("/preview/staff")
        r = as_master.patch(f"/api/casts/{cid}",
                            json={"field": "name", "value": "変えた"})
        assert r.status_code == 403

    def test_外した役割は名乗れない(self, as_master):
        """ひな形の `outside` は外した。**名乗れると、無い役割の画面を確かめたことになる**"""
        as_master.post("/preview/outside")
        with as_master.session_transaction() as s:
            assert s.get("preview_as") is None


class Test無くした役割:
    """**古いデータを弾かない**（土台 §4.1）。"""

    def test_責任者は担当者として扱う(self, app, client):
        from app.security import effective_role
        with app.app_context():
            u = User(email="old@test", name="旧", role="manager")
            u.set_password("password-for-test")
            _db.session.add(u)
            _db.session.commit()
        client.post("/login", data={"email": "old@test",
                                    "password": "password-for-test"})
        assert client.get("/guests").status_code == 200
        assert client.get("/admin").status_code == 403


class Test一覧表:
    def test_判定と表が食い違わない(self, app):
        """**表を手で書かない。**直したときに表だけ古くなる"""
        from app.security import capability_matrix
        rows = {r["what"]: r["by"] for r in capability_matrix()}
        assert rows["利用者の招待・役割の変更"]["owner"] == "○"
        assert rows["利用者の招待・役割の変更"]["staff"] == "×"
        assert rows["機能の公開／非公開の切り替え"]["owner"] == "×"
        assert rows["キャストの追加・編集"]["owner"] == "○"
        assert rows["キャストの追加・編集"]["staff"] == "×"
        assert rows["ゲストの閲覧"]["staff"] == "全件"

    def test_画面に出る(self, as_owner):
        body = as_owner.get("/admin").get_data(as_text=True)
        assert "ロール別にできること" in body

    def test_確かめる欄はマスターだけ(self, as_master, app):
        assert "見え方を確かめる" in as_master.get("/admin").get_data(as_text=True)
        as_master.post("/preview/owner")
        assert "見え方を確かめる" not in as_master.get("/admin").get_data(as_text=True)


class Testマスターを見せない:
    """**開発側の人を先方の一覧に出さない。**混ざると説明が要る。"""

    def _master(self, app):
        with app.app_context():
            u = User(email="dev@test", name="開発", role="master")
            u.set_password("password-for-test")
            _db.session.add(u)
            _db.session.commit()
            return u.id

    def test_一覧に出さない(self, app, as_owner):
        self._master(app)
        body = as_owner.get("/admin").get_data(as_text=True)
        assert "dev@test" not in body

    def test_URLを直に叩いても触れない(self, app, as_owner):
        """**隠すだけでは足りない。**無効にできてしまう"""
        mid = self._master(app)
        r = as_owner.post(f"/admin/users/{mid}", data={"action": "toggle"})
        assert r.status_code == 404
        with app.app_context():
            assert _db.session.get(User, mid).is_active_user is True

    def test_マスターには見える(self, app, as_master):
        assert "master@test" in as_master.get("/admin").get_data(as_text=True)

    def test_確認モード中は隠れる(self, app, as_master):
        as_master.post("/preview/owner")
        assert "master@test" not in as_master.get("/admin").get_data(as_text=True)


class Test自分より上は見せない:
    """**強い役割の一覧が見えると、そこに何があるかを確かめたくなる。**"""

    def test_管理者にマスターの列を出さない(self, as_owner):
        body = as_owner.get("/admin").get_data(as_text=True)
        seg = body[body.index("ロール別にできること"):]
        assert "マスター" not in seg[:seg.index("</table>")]

    def test_マスターだけができる行は消える(self, as_owner):
        body = as_owner.get("/admin").get_data(as_text=True)
        seg = body[body.index("ロール別にできること"):]
        seg = seg[:seg.index("</table>")]
        assert "機能の公開" not in seg
        assert "外部連携の鍵" not in seg

    def test_マスターには全部出る(self, as_master):
        body = as_master.get("/admin").get_data(as_text=True)
        seg = body[body.index("ロール別にできること"):]
        seg = seg[:seg.index("</table>")]
        assert "マスター" in seg and "機能の公開" in seg

    def test_確認モード中も上は見えない(self, as_master):
        as_master.post("/preview/owner")
        body = as_master.get("/admin").get_data(as_text=True)
        seg = body[body.index("ロール別にできること"):]
        assert "マスター" not in seg[:seg.index("</table>")]
