"""見本のドメイン。**一覧・インライン編集・履歴・要確認**の作法。"""
from app.extensions import db as _db
from app.models import ChangeHistory, Partner


def _p(name="取引先", **kw):
    p = Partner(name=name, **kw)
    _db.session.add(p)
    _db.session.commit()
    return p


class Test汚いデータを許す:
    def test_未採番でも登録できる(self, app, db):
        """**NULL可を恐れない。**未採番は正常な状態"""
        with app.app_context():
            p = _p(code=None)
            assert p.id and "未採番" in p.issues

    def test_コードが重複しても登録できる(self, app, db):
        """**一意制約を安易に付けない。**重複が実在する"""
        with app.app_context():
            _p("A", code="100")
            _p("B", code="100")
            assert Partner.query.filter_by(code="100").count() == 2

    def test_要確認だけ絞り込める(self, as_owner, app, db):
        with app.app_context():
            _p("欠けている方", code=None)
            _p("揃っている方", code="1", contact_email="x@test")
        body = as_owner.get("/partners?issues=1").get_data(as_text=True)
        assert "欠けている方" in body and "揃っている方" not in body


class Testインライン編集:
    def test_直せる(self, as_owner, app, db):
        with app.app_context():
            pid = _p().id
        r = as_owner.patch(f"/api/partners/{pid}",
                           json={"field": "name", "value": "直した"})
        assert r.status_code == 200
        with app.app_context():
            assert _db.session.get(Partner, pid).name == "直した"

    def test_必ず履歴を残す(self, as_owner, app, db):
        with app.app_context():
            pid = _p(name="もと").id
        as_owner.patch(f"/api/partners/{pid}",
                       json={"field": "name", "value": "あと"})
        with app.app_context():
            h = ChangeHistory.query.filter_by(entity_type="partner",
                                              field="name").one()
            assert h.old_value == "もと" and h.new_value == "あと"

    def test_ホワイトリスト外は断る(self, as_owner, app, db):
        """**`id` や `archived_at` を書き換えさせない**"""
        with app.app_context():
            pid = _p().id
        r = as_owner.patch(f"/api/partners/{pid}",
                           json={"field": "id", "value": "999"})
        assert r.status_code == 400

    def test_名前は空にできない(self, as_owner, app, db):
        with app.app_context():
            pid = _p(name="消さない").id
        r = as_owner.patch(f"/api/partners/{pid}",
                           json={"field": "name", "value": ""})
        assert r.status_code == 400
        with app.app_context():
            assert _db.session.get(Partner, pid).name == "消さない"

    def test_同じ値なら履歴を作らない(self, as_owner, app, db):
        with app.app_context():
            pid = _p(name="同じ").id
        as_owner.patch(f"/api/partners/{pid}",
                       json={"field": "name", "value": "同じ"})
        with app.app_context():
            assert ChangeHistory.query.filter_by(field="name").count() == 0


class Testアーカイブ:
    def test_消さずに下げる(self, as_owner, app, db):
        """**履歴と集計に要る。**消すと解約率も出せなくなる"""
        with app.app_context():
            pid = _p().id
        as_owner.post(f"/partners/{pid}/archive", follow_redirects=True)
        with app.app_context():
            p = _db.session.get(Partner, pid)
            assert p is not None and p.archived_at is not None

    def test_既定の一覧には出さない(self, as_owner, app, db):
        with app.app_context():
            pid = _p("下げる方").id
        as_owner.post(f"/partners/{pid}/archive", follow_redirects=True)
        assert "下げる方" not in as_owner.get("/partners").get_data(as_text=True)
        assert "下げる方" in as_owner.get("/partners?archived=1").get_data(as_text=True)


class Test一覧の作法:
    def test_並び替えは範囲外を無視する(self, as_owner, app, db):
        """**エラーにしない。**URLを手で書き換えても壊れない"""
        with app.app_context():
            _p()
        assert as_owner.get("/partners?sort=よその列").status_code == 200

    def test_未採番を落とさない(self, as_owner, app, db):
        """**空を落とすと、直すべきものが一覧から消える**"""
        with app.app_context():
            _p("採番なし", code=None)
            _p("採番あり", code="1")
        body = as_owner.get("/partners?sort=code").get_data(as_text=True)
        assert "採番なし" in body and "採番あり" in body

    def test_探せる(self, as_owner, app, db):
        with app.app_context():
            _p("あたり")
            _p("はずれ")
        body = as_owner.get("/partners?q=あたり").get_data(as_text=True)
        assert "あたり" in body and "はずれ" not in body
