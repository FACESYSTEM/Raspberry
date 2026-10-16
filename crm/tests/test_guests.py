"""ゲスト。**Notion の書き出しで実際に見た汚れを、壊さずに持てること。**"""
from datetime import date, timedelta

from app.extensions import db as _db
from app.main import today_jst
from app.models import (Assignment, Bottle, Cast, ChangeHistory, Corp, Guest,
                        Visit)


def _g(name="見本 太郎", **kw):
    x = Guest(name=name, **kw)
    _db.session.add(x)
    _db.session.commit()
    return x


def _cast(name="見本あい", no=1):
    c = Cast(name=name, cast_no=no)
    _db.session.add(c)
    _db.session.commit()
    return c


def _patch(cl, gid, field, value):
    return cl.patch(f"/api/guests/{gid}", json={"field": field, "value": value})


class Test汚いデータを許す:
    def test_番号なしでも登録できる(self, app, db):
        """**NULL可を恐れない。**Notion で空が2件あった"""
        x = _g(guest_no=None)
        assert x.id and "番号なし" in x.issues

    def test_番号が重複しても登録できる(self, app, db):
        """**一意制約を付けない。**Notion で 2081 が2件あった"""
        _g("A", guest_no=2081)
        _g("B", guest_no=2081)
        assert Guest.query.filter_by(guest_no=2081).count() == 2

    def test_重複は要確認に出る(self, app, db):
        a = _g("A", guest_no=2081)
        _g("B", guest_no=2081)
        c = _g("C", guest_no=2082)
        assert "番号が重複" in a.issues
        assert "番号が重複" not in c.issues

    def test_名前なしでも登録できる(self, app, db):
        """Notion で名前の無い行が18件あった"""
        x = _g(name=None, guest_no=1)
        assert "名前なし" in x.issues
        assert x.label == "Gu_1"

    def test_画面からも番号だけで足せる(self, as_staff, app):
        as_staff.post("/guests/new", data={"guest_no": "Gu_2696", "name": ""})
        with app.app_context():
            assert Guest.query.filter_by(guest_no=2696).one().name is None

    def test_番号も名前も無ければ足さない(self, as_staff, app):
        as_staff.post("/guests/new", data={"guest_no": "", "name": ""})
        with app.app_context():
            assert Guest.query.count() == 0


class Test導いて出す:
    """**列で持たない。**両方持つと片方だけ古くなる（規約 §7.6）"""

    def test_画面の名前は番号と名前から作る(self, app, db):
        x = _g("山田", guest_no=1592)
        assert x.label == "Gu_1592 山田"
        x.name = "山田 太郎"
        assert x.label == "Gu_1592 山田 太郎"

    def test_最終来店日と回数は来店から出す(self, app, db):
        x = _g()
        _db.session.add_all([Visit(guest=x, visited_on=date(2026, 9, 1)),
                             Visit(guest=x, visited_on=date(2026, 10, 1))])
        _db.session.commit()
        assert x.last_visit_on == date(2026, 10, 1) and x.visit_count == 2

    def test_取り消した来店は数えない(self, as_staff, app):
        with app.app_context():
            x = _g()
            v = Visit(guest=x, visited_on=date(2026, 10, 1))
            _db.session.add(v)
            _db.session.commit()
            gid, vid = x.id, v.id
        as_staff.post(f"/visits/{vid}/cancel")
        with app.app_context():
            x = _db.session.get(Guest, gid)
            assert x.visit_count == 0 and x.last_visit_on is None
            # **消してはいない**
            assert _db.session.get(Visit, vid) is not None

    def test_来店の記録が無いことを来ていないと書かない(self, as_staff, app):
        with app.app_context():
            gid = _g().id
        body = as_staff.get(f"/guests/{gid}").get_data(as_text=True)
        assert "来店の記録はありません" in body


class Test誕生日:
    """**年は分からないことが多い。**Notion では76件すべて2026年だった"""

    def test_年が無くても月日は持てる(self, as_staff, app):
        with app.app_context():
            gid = _g().id
        assert _patch(as_staff, gid, "birth_month", "4").status_code == 200
        assert _patch(as_staff, gid, "birth_day", "8").status_code == 200
        with app.app_context():
            x = _db.session.get(Guest, gid)
            assert x.birth_year is None
            assert x.birthday_text == "4月8日"
            assert x.age is None          # **年が分からなければ年齢を出さない**

    def test_範囲外の月は断る(self, as_staff, app):
        with app.app_context():
            gid = _g().id
        r = _patch(as_staff, gid, "birth_month", "13")
        assert r.status_code == 400 and r.get_json()["error"] == "validation"

    def test_暦に無い日は断る(self, as_staff, app):
        with app.app_context():
            gid = _g(birth_month=4).id
        assert _patch(as_staff, gid, "birth_day", "31").status_code == 400
        with app.app_context():
            assert _db.session.get(Guest, gid).birth_day is None

    def test_年が分からなければ2月29日も通す(self, as_staff, app):
        with app.app_context():
            gid = _g(birth_month=2).id
        assert _patch(as_staff, gid, "birth_day", "29").status_code == 200

    def test_日が分からないと要確認(self, app, db):
        x = _g(birth_month=1)
        assert "誕生日の日が分からない" in x.issues

    def test_誕生月はダッシュボードに出る(self, as_staff, app):
        t = today_jst()
        with app.app_context():
            _g("誕生月の人", birth_month=t.month, birth_day=1)
            _g("別の月の人", birth_month=(t.month + 5) % 12 + 1, birth_day=1)
        body = as_staff.get("/").get_data(as_text=True)
        assert "誕生月の人" in body and "別の月の人" not in body


class Test担当:
    """**「担当なし」と「分からない」を分ける**（規約 §7.3）"""

    def test_記録が無ければ分からない(self, app, db):
        x = _g()
        assert x.cast_state == "unknown"
        assert "担当が分からない" in x.issues

    def test_担当なしを確かめたら要確認から外れる(self, as_staff, app):
        with app.app_context():
            gid = _g().id
        as_staff.post(f"/guests/{gid}/no_cast")
        with app.app_context():
            x = _db.session.get(Guest, gid)
            assert x.cast_state == "none" and "担当が分からない" not in x.issues

    def test_担当を付けられる(self, as_staff, app):
        with app.app_context():
            gid, cid = _g().id, _cast().id
        as_staff.post(f"/guests/{gid}/assignments",
                      data={"cast_id": str(cid), "level": "メイン", "mark": "A"})
        with app.app_context():
            x = _db.session.get(Guest, gid)
            a = x.active_assignments[0]
            assert (a.cast_id, a.level, a.mark) == (cid, "メイン", "A")
            assert x.cast_state == "has"

    def test_担当レベルに既定値を置かない(self, as_staff, app):
        """**未選択は未選択として持つ**（規約 §8.5）"""
        with app.app_context():
            gid, cid = _g().id, _cast().id
        as_staff.post(f"/guests/{gid}/assignments", data={"cast_id": str(cid)})
        with app.app_context():
            assert Assignment.query.one().level is None

    def test_1人に複数の担当(self, as_staff, app):
        with app.app_context():
            gid = _g().id
            c1, c2 = _cast("あい", 1).id, _cast("かな", 2).id
        for c in (c1, c2):
            as_staff.post(f"/guests/{gid}/assignments", data={"cast_id": str(c)})
        with app.app_context():
            assert len(_db.session.get(Guest, gid).active_assignments) == 2

    def test_担当を付けたら担当なしは外れる(self, as_staff, app):
        with app.app_context():
            gid, cid = _g(no_cast=True).id, _cast().id
        as_staff.post(f"/guests/{gid}/assignments", data={"cast_id": str(cid)})
        with app.app_context():
            assert _db.session.get(Guest, gid).no_cast is False

    def test_外しても消さない(self, as_staff, app):
        """**誰がいつまで担当していたかを残す。**引き継ぎで要る"""
        with app.app_context():
            x, c = _g(), _cast()
            a = Assignment(guest=x, cast=c)
            _db.session.add(a)
            _db.session.commit()
            gid, aid = x.id, a.id
        as_staff.post(f"/assignments/{aid}/end")
        with app.app_context():
            a = _db.session.get(Assignment, aid)
            assert a is not None and a.ended_at is not None
            assert _db.session.get(Guest, gid).cast_state == "unknown"

    def test_担当がいるのに担当なしにはできない(self, as_staff, app):
        with app.app_context():
            x, c = _g(), _cast()
            _db.session.add(Assignment(guest=x, cast=c))
            _db.session.commit()
            gid = x.id
        as_staff.post(f"/guests/{gid}/no_cast")
        with app.app_context():
            assert _db.session.get(Guest, gid).no_cast is False


class Testボトル:
    def test_書かれたとおりに残す(self, as_staff, app):
        """**読み違いに備えて、元の書き方を必ず残す**"""
        with app.app_context():
            gid = _g().id
        as_staff.post(f"/guests/{gid}/bottles",
                      data={"label": "マッカラン⑩P", "brand": "マッカラン",
                            "keep_no": "10"})
        with app.app_context():
            b = Bottle.query.one()
            assert (b.label, b.brand, b.keep_no) == ("マッカラン⑩P", "マッカラン", 10)

    def test_銘柄が分からなくても足せる(self, as_staff, app):
        with app.app_context():
            gid = _g().id
        as_staff.post(f"/guests/{gid}/bottles", data={"label": "現物裏2段目シーバス"})
        with app.app_context():
            assert Bottle.query.one().brand is None

    def test_飲み切っても消さない(self, as_staff, app):
        with app.app_context():
            x = _g()
            b = Bottle(guest=x, label="知多⑯P")
            _db.session.add(b)
            _db.session.commit()
            gid, bid = x.id, b.id
        as_staff.post(f"/bottles/{bid}/finish")
        with app.app_context():
            assert _db.session.get(Bottle, bid).finished_at is not None
            assert _db.session.get(Guest, gid).keeping_bottles == []

    def test_ボトルでも探せる(self, as_staff, app):
        with app.app_context():
            x = _g("ボトルの人")
            _db.session.add(Bottle(guest=x, label="吉四六P27"))
            _g("ほかの人")
        body = as_staff.get("/guests?q=吉四六").get_data(as_text=True)
        assert "ボトルの人" in body and "ほかの人" not in body


class Test来店:
    def test_記録できる(self, as_staff, app):
        with app.app_context():
            gid = _g().id
        as_staff.post(f"/guests/{gid}/visits", data={"visited_on": "2026-10-01"})
        with app.app_context():
            assert Visit.query.one().visited_on == date(2026, 10, 1)

    def test_先の日付は入れない(self, as_staff, app):
        with app.app_context():
            gid = _g().id
        tomorrow = today_jst() + timedelta(days=1)
        as_staff.post(f"/guests/{gid}/visits",
                      data={"visited_on": tomorrow.isoformat()})
        with app.app_context():
            assert Visit.query.count() == 0


class Testインライン編集:
    def test_直せる(self, as_staff, app):
        with app.app_context():
            gid = _g().id
        assert _patch(as_staff, gid, "kana", "ヤマダ").status_code == 200
        with app.app_context():
            assert _db.session.get(Guest, gid).kana == "ヤマダ"

    def test_必ず履歴を残す(self, as_staff, app):
        with app.app_context():
            gid = _g(name="もと").id
        _patch(as_staff, gid, "name", "あと")
        with app.app_context():
            h = ChangeHistory.query.filter_by(entity_type="guest",
                                              field="名前").one()
            assert h.old_value == "もと" and h.new_value == "あと"

    def test_ホワイトリスト外は断る(self, as_staff, app):
        """**`id` や `archived_at` や `no_cast` を書き換えさせない**"""
        with app.app_context():
            gid = _g().id
        for f in ("id", "archived_at", "no_cast"):
            assert _patch(as_staff, gid, f, "1").status_code == 400

    def test_同じ値なら履歴を作らない(self, as_staff, app):
        with app.app_context():
            gid = _g(name="同じ").id
        _patch(as_staff, gid, "name", "同じ")
        with app.app_context():
            assert ChangeHistory.query.filter_by(field="名前").count() == 0

    def test_番号はGu_を付けて書いても数字で持つ(self, as_staff, app):
        with app.app_context():
            gid = _g().id
        _patch(as_staff, gid, "guest_no", "Gu_1592")
        with app.app_context():
            assert _db.session.get(Guest, gid).guest_no == 1592

    def test_空にすると分からないに戻る(self, as_staff, app):
        with app.app_context():
            gid = _g(birth_month=4).id
        _patch(as_staff, gid, "birth_month", "")
        with app.app_context():
            assert _db.session.get(Guest, gid).birth_month is None


class Test法人団体:
    def test_同じ名前なら同じ法人に寄せる(self, as_staff, app):
        with app.app_context():
            a, b = _g("A").id, _g("B").id
        _patch(as_staff, a, "corp", "見本商事株式会社")
        _patch(as_staff, b, "corp", "見本商事株式会社")
        with app.app_context():
            assert Corp.query.count() == 1

    def test_表記ゆれは寄せない(self, as_staff, app):
        """**同じ会社かは人が決める。**黙って寄せると戻せない"""
        with app.app_context():
            a, b = _g("A").id, _g("B").id
        _patch(as_staff, a, "corp", "見本商事株式会社")
        _patch(as_staff, b, "corp", "見本商事㈱")
        with app.app_context():
            assert Corp.query.count() == 2

    def test_法人が無いのにフリガナは入れない(self, as_staff, app):
        with app.app_context():
            gid = _g().id
        assert _patch(as_staff, gid, "corp_kana", "ミホン").status_code == 400


class Test下げる:
    def test_消さずに下げる(self, as_staff, app):
        with app.app_context():
            gid = _g().id
        as_staff.post(f"/guests/{gid}/archive")
        with app.app_context():
            x = _db.session.get(Guest, gid)
            assert x is not None and x.archived_at is not None

    def test_既定の一覧には出さない(self, as_staff, app):
        with app.app_context():
            gid = _g("下げる方").id
        as_staff.post(f"/guests/{gid}/archive")
        assert "下げる方" not in as_staff.get("/guests").get_data(as_text=True)
        assert "下げる方" in as_staff.get("/guests?archived=1").get_data(as_text=True)

    def test_下げたゲストの番号は重複に数えない(self, as_staff, app):
        with app.app_context():
            a = _g("残る方", guest_no=5)
            b = _g("下げる方", guest_no=5)
            aid, bid = a.id, b.id
        as_staff.post(f"/guests/{bid}/archive")
        with app.app_context():
            assert "番号が重複" not in _db.session.get(Guest, aid).issues


class Test一覧の作法:
    def test_並び替えは範囲外を無視する(self, as_staff, app):
        with app.app_context():
            _g()
        assert as_staff.get("/guests?sort=よその列").status_code == 200

    def test_番号なしを落とさない(self, as_staff, app):
        with app.app_context():
            _g("番号なしの人", guest_no=None)
            _g("番号ありの人", guest_no=1)
        for d in ("asc", "desc"):
            body = as_staff.get(f"/guests?sort=guest_no&dir={d}").get_data(as_text=True)
            assert "番号なしの人" in body and "番号ありの人" in body

    def test_最終来店で並べても落ちない(self, as_staff, app):
        with app.app_context():
            x = _g("来た人")
            _db.session.add(Visit(guest=x, visited_on=date(2026, 10, 1)))
            _g("記録なしの人")
        body = as_staff.get("/guests?sort=last_visit&dir=desc").get_data(as_text=True)
        assert body.index("来た人") < body.index("記録なしの人")

    def test_要確認を種類で絞れる(self, as_staff, app):
        """**合計だけでなく内訳から辿れる**（ダッシュボードから飛んでくる）"""
        with app.app_context():
            _g("番号なしの人", guest_no=None, no_cast=True)
            _g("担当不明の人", guest_no=1)
        body = as_staff.get("/guests?issue=番号なし").get_data(as_text=True)
        assert "番号なしの人" in body and "担当不明の人" not in body

    def test_番号で探せる(self, as_staff, app):
        with app.app_context():
            _g("あたり", guest_no=1592)
            _g("はずれ", guest_no=1593)
        body = as_staff.get("/guests?q=Gu_1592").get_data(as_text=True)
        assert "あたり" in body and "はずれ" not in body

    def test_要確認の内訳がダッシュボードに出る(self, as_staff, app):
        with app.app_context():
            _g(guest_no=None)
        body = as_staff.get("/").get_data(as_text=True)
        assert "番号なし" in body and "担当が分からない" in body

    def test_降順でも空は最後(self, as_staff, app):
        """**向きを変えたら空が先頭に来た**、を起こさない"""
        with app.app_context():
            _g("番号なしの人", guest_no=None)
            _g("番号ありの人", guest_no=1)
        body = as_staff.get("/guests?sort=guest_no&dir=desc").get_data(as_text=True)
        assert body.index("番号ありの人") < body.index("番号なしの人")
