"""自社の情報。**設定したのに効かない、を防ぐ。**

`.env` と DB で二重に持つと、管理画面で変えても画面が変わらない。
設定した側から見て原因が分からないので、いちばん厄介な壊れ方をする。
"""
from app.models import COMPANIES, COMPANY_LABEL, OrgProfile


class Test複数の法人:
    def test_2つぶん持てる(self, app, db):
        """請求書が「株式会社計上」「合同会社計上」に分かれている。

        1行しか持てないと、請求書の発行元を出し分けられない。
        """
        rows = OrgProfile.all_companies()
        assert {r.code for r in rows} == {"main", "sub"}

    def test_無ければ作る(self, app, db):
        OrgProfile.query.delete()
        db.session.commit()
        assert len(OrgProfile.all_companies()) == 2

    def test_何度呼んでも増えない(self, app, db):
        OrgProfile.all_companies()
        OrgProfile.all_companies()
        assert OrgProfile.query.count() == 2

    def test_請求書に要るものが空なら要確認に出す(self, app, db):
        """埋まっていないと請求書が出せない。**先に気づけるようにする。**"""
        kk = next(r for r in OrgProfile.all_companies() if r.code == "main")
        assert "適格請求書発行事業者の登録番号が未記入" in kk.issues
        assert "振込先が未記入" in kk.issues


class Test画面の名前:
    def test_DBの名前が画面に出る(self, app, db, as_owner):
        """**二重持ちをやめた。**管理画面で変えたら画面も変わる。"""
        o = OrgProfile.display()
        o.name = "変更後の名前"
        db.session.commit()
        assert "変更後の名前" in as_owner.get("/partners").get_data(as_text=True)

    def test_表示する法人は1つだけ(self, app, db, as_owner):
        """複数立つと、どちらが出るか分からなくなる。"""
        rows = OrgProfile.all_companies()
        gk = next(r for r in rows if r.code == "sub")
        as_owner.post(f"/admin/org/{gk.id}",
                      data={"name": "別法人", "is_display": "1"})
        assert sum(1 for r in OrgProfile.query.all() if r.is_display) == 1
        assert OrgProfile.display().code == "sub"

    def test_法人ごとに保存される(self, app, db, as_owner):
        """振込先を取り違えると入金されない。**混ざらないことを固定する。**"""
        rows = OrgProfile.all_companies()
        kk = next(r for r in rows if r.code == "main")
        gk = next(r for r in rows if r.code == "sub")
        as_owner.post(f"/admin/org/{kk.id}", data={"bank": "A銀行 1111111"})
        as_owner.post(f"/admin/org/{gk.id}", data={"bank": "B銀行 2222222"})
        assert db.session.get(OrgProfile, kk.id).bank == "A銀行 1111111"
        assert db.session.get(OrgProfile, gk.id).bank == "B銀行 2222222"

    def test_変更が履歴に残る(self, app, db, as_owner):
        from app.models import ChangeHistory
        o = OrgProfile.display()
        as_owner.post(f"/admin/org/{o.id}", data={"tel": "092-000-0000"})
        h = ChangeHistory.query.filter_by(entity_type="org").first()
        assert h.field == "tel" and h.new_value == "092-000-0000"

    def test_管理者以外は触れない(self, app, db, as_staff):
        o = OrgProfile.display()
        assert as_staff.post(f"/admin/org/{o.id}",
                             data={"name": "書き換え"}).status_code == 403

    def test_送られてこない項目は消さない(self, app, db, as_owner):
        """一部だけ送っただけで他の値が消えると、気づかないまま情報が失われる。

        （土台 §4.4「人が入れた値を上書きしない」）
        """
        o = OrgProfile.display()
        o.name = "自社"
        o.bank = "A銀行 1111111"
        db.session.commit()
        as_owner.post(f"/admin/org/{o.id}", data={"tel": "092-000-0000"})
        got = db.session.get(OrgProfile, o.id)
        assert got.name == "自社"
        assert got.bank == "A銀行 1111111"
        assert got.tel == "092-000-0000"


class Test使っていない法人:
    def test_要確認を出さない(self, app, db):
        """直しようのない警告を出し続けると、全体が読まれなくなる（土台 §3.4）。"""
        gk = next(r for r in OrgProfile.all_companies() if r.code == "sub")
        assert gk.issues                      # 使っているうちは出る
        gk.in_use = False
        db.session.commit()
        assert gk.issues == []                # 使っていなければ出さない

    def test_行は消さない(self, app, db):
        """過去の請求書の発行元として、情報が要る。"""
        gk = next(r for r in OrgProfile.all_companies() if r.code == "sub")
        gk.in_use = False
        gk.bank = "旧口座"
        db.session.commit()
        assert db.session.get(OrgProfile, gk.id).bank == "旧口座"
        assert len(OrgProfile.all_companies()) == 2

    def test_画面から切り替えられる(self, app, db, as_owner):
        gk = next(r for r in OrgProfile.all_companies() if r.code == "sub")
        as_owner.post(f"/admin/org/{gk.id}", data={"_has_in_use": "1"})   # 外す
        assert db.session.get(OrgProfile, gk.id).in_use is False
        as_owner.post(f"/admin/org/{gk.id}", data={"_has_in_use": "1", "in_use": "1"})
        assert db.session.get(OrgProfile, gk.id).in_use is True

    def test_切り替えが履歴に残る(self, app, db, as_owner):
        from app.models import ChangeHistory
        gk = next(r for r in OrgProfile.all_companies() if r.code == "sub")
        as_owner.post(f"/admin/org/{gk.id}", data={"_has_in_use": "1"})
        h = ChangeHistory.query.filter_by(entity_type="org", field="利用").first()
        assert h is not None and h.new_value == "使っていない"

    def test_畳んでも登録番号は消えない(self, app, db, as_owner):
        """画面から欄が消えても、**送られてこない項目は触らない**（土台 §4.4）。

        過去の請求書を出し直すときに要る。
        """
        gk = next(r for r in OrgProfile.all_companies() if r.code == "sub")
        gk.invoice_reg_no = "T9999999999999"
        gk.bank = "旧口座 1234567"
        db.session.commit()
        as_owner.post(f"/admin/org/{gk.id}",
                      data={"_has_in_use": "1", "name": "別法人"})
        got = db.session.get(OrgProfile, gk.id)
        assert got.in_use is False
        assert got.invoice_reg_no == "T9999999999999"
        assert got.bank == "旧口座 1234567"


class Test別ページ:
    """**一度決めたらほとんど見ないもの**は、毎日見る画面に置かない。"""

    def test_一覧には設定欄を出さない(self, as_owner):
        body = as_owner.get("/admin").get_data(as_text=True)
        assert "請求書に出るもの" not in body
        assert "/admin/org" in body

    def test_別ページで設定できる(self, as_owner):
        body = as_owner.get("/admin/org").get_data(as_text=True)
        assert "請求書に出るもの" in body

    def test_保存すると元のページに戻る(self, as_owner, app):
        from app.models import OrgProfile
        with app.app_context():
            oid = OrgProfile.all_companies()[0].id
        r = as_owner.post(f"/admin/org/{oid}", data={"tel": "092-000-0000"})
        assert r.headers["Location"].endswith("/admin/org")

    def test_担当者は開けない(self, as_staff):
        assert as_staff.get("/admin/org").status_code == 403


class Testメールアドレスを直す:
    """**打ち間違いは必ず起きる。**実際に1件あった。
    その都度こちらに依頼が要る形にしない。
    """

    def _user(self, app, email="typo@test", role="staff"):
        from app.extensions import db as _db
        from app.models import User
        with app.app_context():
            u = User(email=email, name="見本", role=role)
            _db.session.add(u)
            _db.session.commit()
            return u.id

    def test_直せる(self, as_owner, app):
        from app.extensions import db as _db
        from app.models import User
        uid = self._user(app)
        as_owner.post(f"/admin/users/{uid}",
                      data={"action": "email", "email": "right@test"})
        with app.app_context():
            assert _db.session.get(User, uid).email == "right@test"

    def test_重なったら断る(self, as_owner, app):
        from app.extensions import db as _db
        from app.models import User
        uid = self._user(app)
        as_owner.post(f"/admin/users/{uid}",
                      data={"action": "email", "email": "owner@test"})
        with app.app_context():
            assert _db.session.get(User, uid).email == "typo@test"

    def test_誰と重なったかは出さない(self, as_owner, app):
        """**登録されている人を調べる手がかりにしない。**"""
        uid = self._user(app)
        r = as_owner.post(f"/admin/users/{uid}",
                          data={"action": "email", "email": "owner@test"},
                          follow_redirects=True)
        assert "owner@test" not in r.get_data(as_text=True).split("<form")[0]

    def test_形になっていなければ断る(self, as_owner, app):
        from app.extensions import db as _db
        from app.models import User
        uid = self._user(app)
        as_owner.post(f"/admin/users/{uid}",
                      data={"action": "email", "email": "でたらめ"})
        with app.app_context():
            assert _db.session.get(User, uid).email == "typo@test"

    def test_履歴に残る(self, as_owner, app):
        from app.models import ChangeHistory
        uid = self._user(app)
        as_owner.post(f"/admin/users/{uid}",
                      data={"action": "email", "email": "right@test"})
        with app.app_context():
            h = ChangeHistory.query.filter_by(entity_type="user", entity_id=uid,
                                              field="メールアドレス").one()
            assert h.old_value == "typo@test" and h.new_value == "right@test"

    def test_担当者は直せない(self, as_staff, app):
        uid = self._user(app)
        assert as_staff.post(f"/admin/users/{uid}",
                             data={"action": "email", "email": "x@test"}).status_code == 403

    def test_マスターの分は触れない(self, as_owner, app):
        """**隠すだけでは足りない。**URLを直に叩けば変えられてしまう"""
        from app.extensions import db as _db
        from app.models import User
        uid = self._user(app, email="dev@test", role="master")
        r = as_owner.post(f"/admin/users/{uid}",
                          data={"action": "email", "email": "x@test"})
        assert r.status_code == 404
        with app.app_context():
            assert _db.session.get(User, uid).email == "dev@test"
