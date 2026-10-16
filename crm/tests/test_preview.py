"""見え方の確認モード。**確認したつもりが、確認になっていない**を防ぐ。

ここが緩むと「マスターだから見えていただけ」に気づけないまま公開してしまう。
"""
import pytest

from app.extensions import db as _db
from app.models import Feature, Partner, User


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

    def test_社外では書けない(self, as_master, app):
        with app.app_context():
            c = Partner(name="見本商事")
            _db.session.add(c)
            _db.session.commit()
            cid = c.id
        as_master.post("/preview/outside")
        r = as_master.patch(f"/api/partners/{cid}",
                            json={"field": "name", "value": "変えた"})
        assert r.status_code == 403


def _outside(app, released):
    """社外の方の画面の公開／非公開を切り替える。

    テストの土台は**全機能を公開**にしてあるので、
    「まだ出さない」を確かめるときは、ここで閉じてから見る。
    """
    with app.app_context():
        row = Feature.query.filter_by(key="outside").first() or Feature(key="outside")
        row.released = released
        _db.session.add(row)
        _db.session.commit()


def _open_outside(app):
    _outside(app, True)


def _rows(resp):
    """顧問先一覧に出ている件数。**名前で数えない。**

    画面の帯に確認中の顧問先名が出るため、名前で数えると
    「1件も出ていない」を「出ている」と読み違える。
    """
    import re
    return len(re.findall(r'href="/partners/\d+"',
                          resp.get_data(as_text=True)))


class Test社外の方はまだ出さない:
    """**器だけ用意した状態。**何を見せるかが決まる前に見せない。"""

    @pytest.fixture(autouse=True)
    def _closed(self, app):
        _outside(app, False)

    def test_社外には何も出さない(self, as_master, app):
        with app.app_context():
            c = Partner(name="見本商事")
            _db.session.add(c)
            _db.session.commit()
            cid = c.id
        as_master.post("/preview/outside", data={"scope_id": str(cid)})
        assert _rows(as_master.get("/partners")) == 0

    def test_社外にも何も出さない(self, as_master, app):
        with app.app_context():
            _db.session.add(Partner(name="見本商事"))
            _db.session.commit()
        as_master.post("/preview/outside")
        assert _rows(as_master.get("/partners")) == 0

    def test_自社の人には出る(self, as_master, app):
        with app.app_context():
            _db.session.add(Partner(name="見本商事"))
            _db.session.commit()
        as_master.post("/preview/staff")
        assert _rows(as_master.get("/partners")) == 1


class Test公開したあとの範囲:
    """**公開に切り替えた瞬間に、範囲が正しいこと。**

    切り替えてから確かめるのでは遅い。切り替えが漏洩になる。
    """

    def _two(self, app):
        with app.app_context():
            a, b = Partner(name="見本商事"), Partner(name="試作工業")
            _db.session.add_all([a, b])
            _db.session.commit()
            return a.id, b.id

    def test_自分の分だけ(self, as_master, app):
        aid, bid = self._two(app)
        _open_outside(app)
        as_master.post("/preview/outside", data={"scope_id": str(aid)})
        assert _rows(as_master.get("/partners")) == 1
        assert as_master.get(f"/partners/{bid}").status_code == 404

    def test_紐づけが無ければ1件も出さない(self, as_master, app):
        """**紐づけ忘れ＝全部見える、が一番まずい。**"""
        self._two(app)
        _open_outside(app)
        as_master.post("/preview/outside", data={"scope_id": ""})
        assert _rows(as_master.get("/partners")) == 0

    def test_社外は自分の分だけ(self, as_master, app):
        aid, bid = self._two(app)
        _open_outside(app)
        as_master.post("/preview/outside", data={"scope_id": str(aid)})
        assert _rows(as_master.get("/partners")) == 1
        assert as_master.get(f"/partners/{bid}").status_code == 404

    def test_選ばなければ1件も出さない(self, as_master, app):
        self._two(app)
        _open_outside(app)
        as_master.post("/preview/outside", data={"scope_id": ""})
        assert _rows(as_master.get("/partners")) == 0

    def test_社外は書けない(self, as_master, app):
        aid, _ = self._two(app)
        _open_outside(app)
        as_master.post("/preview/outside", data={"scope_id": str(aid)})
        r = as_master.patch(f"/api/partners/{aid}",
                            json={"field": "name", "value": "変えた"})
        assert r.status_code == 403


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
        assert client.get("/partners").status_code == 200
        assert client.get("/admin").status_code == 403


class Test一覧表:
    def test_判定と表が食い違わない(self, app):
        """**表を手で書かない。**直したときに表だけ古くなる"""
        from app.security import capability_matrix
        rows = {r["what"]: r["by"] for r in capability_matrix()}
        assert rows["利用者の招待・役割の変更"]["owner"] == "○"
        assert rows["利用者の招待・役割の変更"]["staff"] == "×"
        assert rows["機能の公開／非公開の切り替え"]["owner"] == "×"
        assert rows["取引先の閲覧"]["outside"] == "自分の分だけ"

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
