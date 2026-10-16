"""自社の情報。**設定したのに効かない、を防ぐ。**

`.env` と DB で二重に持つと、管理画面で変えても画面が変わらない。
設定した側から見て原因が分からないので、いちばん厄介な壊れ方をする。

この店では請求書を作らないので、ひな形の請求書の項目（登録番号・振込先）と
2法人目は外した。
"""
from app.models import COMPANIES, OrgProfile


class Test自社:
    def test_1つだけ持つ(self, app, db):
        rows = OrgProfile.all_companies()
        assert [r.code for r in rows] == list(COMPANIES) == ["main"]

    def test_無ければ作る(self, app, db):
        OrgProfile.query.delete()
        db.session.commit()
        assert len(OrgProfile.all_companies()) == 1

    def test_何度呼んでも増えない(self, app, db):
        OrgProfile.all_companies()
        OrgProfile.all_companies()
        assert OrgProfile.query.count() == 1

    def test_名前が空なら要確認に出す(self, app, db):
        """**仮の名前で埋めない。**「自社」と出続けて、未記入に気づけない"""
        o = OrgProfile.display()
        assert o.name is None and o.issues == ["名称が未記入"]

    def test_名前があれば警告しない(self, app, db):
        """住所などは無くても困らない。**直さなくても困らない警告は出さない**"""
        o = OrgProfile.display()
        o.name = "見本の店"
        db.session.commit()
        assert o.issues == []

    def test_未記入はサイドバーに数が出る(self, as_owner):
        assert '<span class="badge warn">1</span>' in as_owner.get("/").get_data(as_text=True)


class Test画面の名前:
    def test_DBの名前が画面に出る(self, app, db, as_owner):
        """**二重持ちをやめた。**管理画面で変えたら画面も変わる。"""
        o = OrgProfile.display()
        o.name = "変更後の名前"
        db.session.commit()
        assert "変更後の名前" in as_owner.get("/guests").get_data(as_text=True)

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
        o.name = "見本の店"
        o.address = "見本市1-1"
        db.session.commit()
        as_owner.post(f"/admin/org/{o.id}", data={"tel": "092-000-0000"})
        got = db.session.get(OrgProfile, o.id)
        assert got.name == "見本の店"
        assert got.address == "見本市1-1"
        assert got.tel == "092-000-0000"


class Test請求書は扱わない:
    def test_請求書の欄を出さない(self, as_owner):
        body = as_owner.get("/admin/org").get_data(as_text=True)
        assert "請求書" not in body and "振込先" not in body

    def test_送られても保存しない(self, app, db, as_owner):
        """**欄を消しただけでは足りない。**送れば入る経路を残さない"""
        o = OrgProfile.display()
        as_owner.post(f"/admin/org/{o.id}", data={"bank": "A銀行"})
        assert not hasattr(db.session.get(OrgProfile, o.id), "bank")


class Test別ページ:
    """**一度決めたらほとんど見ないもの**は、毎日見る画面に置かない。"""

    def test_一覧には設定欄を出さない(self, as_owner):
        body = as_owner.get("/admin").get_data(as_text=True)
        assert 'name="postal_code"' not in body
        assert "/admin/org" in body

    def test_別ページで設定できる(self, as_owner):
        body = as_owner.get("/admin/org").get_data(as_text=True)
        assert 'name="postal_code"' in body

    def test_保存すると元のページに戻る(self, as_owner, app):
        with app.app_context():
            oid = OrgProfile.all_companies()[0].id
        r = as_owner.post(f"/admin/org/{oid}", data={"tel": "092-000-0000"})
        assert r.headers["Location"].endswith("/admin/org")

    def test_スタッフは開けない(self, as_staff):
        assert as_staff.get("/admin/org").status_code == 403
