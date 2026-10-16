"""時刻。**保存はUTC、画面はJST。**

ここが狂うと、変更履歴の「いつ誰が変えたか」が9時間ずれる。
ずれた履歴は、数字が食い違ったときに原因へ辿り着く役に立たない。
"""
import warnings
from datetime import datetime, timezone

from app.models import ChangeHistory, Partner
from app.timeutil import JST, fmt, to_jst, utcnow


class Test保存する値:
    def test_タイムゾーン無しで返す(self):
        """DBの列に合わせる。付いていると比較や保存で落ちる。"""
        assert utcnow().tzinfo is None

    def test_UTCである(self):
        """JSTを保存してしまうと、既存データと9時間ずれる。"""
        got = utcnow()
        expect = datetime.now(timezone.utc).replace(tzinfo=None)
        assert abs((expect - got).total_seconds()) < 5

    def test_非推奨の警告を出さない(self):
        """datetime.utcnow() は将来のPythonで消える。使っていないことを固定する。"""
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            utcnow()


class Test画面に出す値:
    def test_9時間進める(self):
        got = to_jst(datetime(2026, 8, 18, 0, 30))
        assert (got.hour, got.day) == (9, 18)

    def test_日付をまたぐ(self):
        """UTCの深夜は、JSTでは翌日の朝。ここを間違えると日付が1日ずれる。"""
        assert fmt(datetime(2026, 8, 17, 23, 0)) == "2026-08-18 08:00"

    def test_未設定は消さずに印を出す(self):
        """空欄に何も出さない画面は「入っていないのか壊れたのか」が読めない。"""
        assert fmt(None) == "—"
        assert to_jst(None) is None

    def test_タイムゾーン付きが来ても落ちない(self):
        """外から来た値が混ざっても例外にしない。"""
        assert fmt(datetime(2026, 8, 17, 23, 0, tzinfo=timezone.utc)) == "2026-08-18 08:00"

    def test_JSTは9時間固定(self):
        """日本にサマータイムは無い。"""
        assert JST.utcoffset(None).total_seconds() == 9 * 3600


class Test画面での表示:
    def test_変更履歴がJSTで出る(self, app, db, client):
        """値が入った状態で描画する。空の画面だけ見ても分からない。"""
        from tests.conftest import _login
        _login(app, client, "owner")
        with app.app_context():
            p = Partner(name="テスト商事")
            db.session.add(p)
            db.session.commit()
            h = ChangeHistory.record("partner", p.id, "name", "旧", "新")
            h.created_at = datetime(2026, 8, 17, 23, 0)     # UTC
            db.session.commit()
            pid = p.id
        body = client.get(f"/partners/{pid}").get_data(as_text=True)
        assert "2026-08-18 08:00" in body      # JST
        assert "2026-08-17 23:00" not in body  # UTCが漏れていない
