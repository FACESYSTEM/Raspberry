"""キャスト。**番号の無いキャストが実在する。辞めても担当の記録は残す。**"""
from app.extensions import db as _db
from app.models import Assignment, Cast, ChangeHistory, Guest


def _cast(name="見本あい", no=1):
    c = Cast(name=name, cast_no=no)
    _db.session.add(c)
    _db.session.commit()
    return c


class Test表記:
    def test_Notionと同じ書き方で出す(self, app, db):
        assert _cast("あい", 25).label == "CA_25_あい"

    def test_番号なしも登録できる(self, app, db):
        """Notion に `CA_Noなし_〇〇` が実在する"""
        c = _cast("さき", None)
        assert c.label == "CA_Noなし_さき" and "キャスト番号なし" in c.issues


class Test誰が直せるか:
    def test_管理者は足せる(self, as_owner, app):
        as_owner.post("/casts/new", data={"name": "あい", "cast_no": "3"})
        with app.app_context():
            assert Cast.query.one().cast_no == 3

    def test_スタッフは足せない(self, as_staff, app):
        assert as_staff.post("/casts/new", data={"name": "あい"}).status_code == 403
        with app.app_context():
            assert Cast.query.count() == 0

    def test_スタッフは直せない(self, as_staff, app):
        with app.app_context():
            cid = _cast().id
        r = as_staff.patch(f"/api/casts/{cid}", json={"field": "name", "value": "変えた"})
        assert r.status_code == 403

    def test_スタッフも一覧は見られる(self, as_staff, app):
        with app.app_context():
            _cast("見えるあい")
        assert "見えるあい" in as_staff.get("/casts").get_data(as_text=True)

    def test_スタッフには直せるように見せない(self, as_staff, app):
        """**押せないものを押せるように見せない**"""
        with app.app_context():
            cid = _cast().id
        body = as_staff.get(f"/casts/{cid}").get_data(as_text=True)
        assert 'class="ed"' not in body and "/casts/new" not in body

    def test_直したら履歴に残る(self, as_owner, app):
        with app.app_context():
            cid = _cast("もと").id
        as_owner.patch(f"/api/casts/{cid}", json={"field": "name", "value": "あと"})
        with app.app_context():
            h = ChangeHistory.query.filter_by(entity_type="cast", field="名前").one()
            assert (h.old_value, h.new_value) == ("もと", "あと")

    def test_名前は空にできない(self, as_owner, app):
        with app.app_context():
            cid = _cast("消さない").id
        r = as_owner.patch(f"/api/casts/{cid}", json={"field": "name", "value": ""})
        assert r.status_code == 400


class Test在籍:
    def test_外しても担当の記録は残る(self, as_owner, app):
        with app.app_context():
            c = _cast()
            x = Guest(name="見本")
            _db.session.add_all([x, Assignment(guest=x, cast=c)])
            _db.session.commit()
            cid = c.id
        as_owner.post(f"/casts/{cid}/archive")
        with app.app_context():
            c = _db.session.get(Cast, cid)
            assert c.archived_at is not None and len(c.assignments) == 1

    def test_外したキャストは担当の選択肢に出さない(self, as_owner, app):
        with app.app_context():
            c = _cast("辞めたあい")
            gid = Guest(name="見本")
            _db.session.add(gid)
            _db.session.commit()
            cid, gid = c.id, gid.id
        as_owner.post(f"/casts/{cid}/archive")
        body = as_owner.get(f"/guests/{gid}").get_data(as_text=True)
        assert "辞めたあい" not in body

    def test_担当ゲスト数は数えて出す(self, as_owner, app):
        """**列で持たない。**外した担当・下げたゲストは数えない"""
        with app.app_context():
            c = _cast()
            for name, gone in (("A", False), ("B", True)):
                x = Guest(name=name)
                if gone:
                    from app.timeutil import utcnow
                    x.archived_at = utcnow()
                _db.session.add_all([x, Assignment(guest=x, cast=c)])
            _db.session.commit()
            assert len(c.active_assignments) == 1
