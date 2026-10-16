"""修正モード。**気づいた画面で、気づいた瞬間に書けること。**

別紙にまとめて書いてもらう形にすると、その場で忘れる。
"""
from flask import current_app

from app.extensions import db as _db


def _Note():
    """切り出した部品の表。**案件側の対応履歴とは別のもの。**"""
    return current_app.extensions["fixmode"].Note


def _post(cl, body="主担当の欄で迷いました", page="/partners", quote="主担当"):
    return cl.post("/notes", data={"body": body, "page": page,
                                   "page_title": "顧問先", "quote": quote},
                   follow_redirects=True)


class Test書く:
    def test_選んだ文字ごと残る(self, as_owner, app):
        _post(as_owner)
        with app.app_context():
            n = _Note().query.one()
            assert n.quote == "主担当"
            assert n.page == "/partners"
            assert n.body == "主担当の欄で迷いました"

    def test_空では送れない(self, as_owner, app):
        _post(as_owner, body="   ")
        with app.app_context():
            assert _Note().query.count() == 0

    def test_書いた画面に出る(self, as_owner):
        _post(as_owner)
        assert "主担当の欄で迷いました" in as_owner.get("/notes").get_data(as_text=True)

    def test_ほかの画面には出ない(self, as_owner):
        """**その画面のことだけ出す。**全部出すと、どこの話か分からない"""
        _post(as_owner, page="/partners")
        body = as_owner.get("/").get_data(as_text=True)      # 別の画面
        assert "主担当の欄で迷いました" not in body

    def test_社外の方には出さない(self, as_outside):
        body = as_outside.get("/partners").get_data(as_text=True)
        assert 'id="modeswitch"' not in body


class Test潰す:
    def test_一覧に出る(self, as_owner):
        _post(as_owner)
        assert "主担当の欄で迷いました" in as_owner.get("/notes").get_data(as_text=True)

    def test_確認できたら画面から消える(self, as_owner, app):
        _post(as_owner)
        with app.app_context():
            nid = _Note().query.one().id
        as_owner.post(f"/notes/{nid}/accept")
        body = as_owner.get("/partners").get_data(as_text=True)
        assert "主担当の欄で迷いました" not in body

    def test_直しただけでは消えない(self, as_master, app):
        """**確認するまで、その場所を出し続ける。**確認には場所が要る"""
        _post(as_master)
        with app.app_context():
            nid = _Note().query.one().id
        as_master.post(f"/notes/{nid}/fixed", data={"fixed_note": "外しました"})
        assert "主担当の欄で迷いました" in as_master.get("/partners").get_data(as_text=True)

    def test_消さずに残す(self, as_owner, app):
        """**何を言われて何をしたかを残す。**消すと辿れない"""
        _post(as_owner)
        with app.app_context():
            nid = _Note().query.one().id
        as_owner.post(f"/notes/{nid}/accept")
        with app.app_context():
            n = _db.session.get(_Note(), nid)
            assert n is not None and n.done_at is not None and n.state == "done"


class Test直す人と確認する人:
    """**直した本人が閉じられる形にしない。**

    直したつもりが直っていない、が通ってしまう。
    """

    def _one(self, cl, app):
        _post(cl)
        with app.app_context():
            return _Note().query.one().id

    def test_直せるのは当社だけ(self, as_owner, app):
        nid = self._one(as_owner, app)
        assert as_owner.post(f"/notes/{nid}/fixed").status_code == 403

    def test_直すと確認待ちになる(self, as_master, app):
        nid = self._one(as_master, app)
        as_master.post(f"/notes/{nid}/fixed", data={"fixed_note": "外しました"})
        with app.app_context():
            n = _db.session.get(_Note(), nid)
            assert n.state == "fixed" and n.fixed_note == "外しました"
            assert n.done_at is None            # **まだ閉じない**

    def test_差し戻せる(self, as_master, app):
        nid = self._one(as_master, app)
        as_master.post(f"/notes/{nid}/fixed")
        as_master.post(f"/notes/{nid}/reopen",
                       data={"reopen_note": "まだ出ています"})
        with app.app_context():
            n = _db.session.get(_Note(), nid)
            assert n.state == "open" and n.reopen_note == "まだ出ています"

    def test_差し戻すと直した記録は残る(self, as_master, app):
        nid = self._one(as_master, app)
        as_master.post(f"/notes/{nid}/fixed", data={"fixed_note": "外しました"})
        as_master.post(f"/notes/{nid}/reopen", data={"reopen_note": "まだ"})
        with app.app_context():
            assert _db.session.get(_Note(), nid).fixed_at is not None

    def test_確認は先方が行う(self, as_owner, app):
        nid = self._one(as_owner, app)
        assert as_owner.post(f"/notes/{nid}/accept").status_code == 302
        with app.app_context():
            assert _db.session.get(_Note(), nid).state == "done"

    def test_担当者は閉じられない(self, as_staff, app, as_owner):
        nid = self._one(as_owner, app)
        assert as_staff.post(f"/notes/{nid}/accept").status_code == 403

    def test_一覧は管理者だけ(self, as_staff):
        assert as_staff.get("/notes").status_code == 403


class Test対応履歴と混ざらない:
    def test_顧問先の対応履歴に出ない(self, as_owner, app, db):
        """`entity_type` が違う。**画面へのひとことは対応履歴ではない**"""
        from app.models import Partner
        c = Partner(name="見本商事")
        _db.session.add(c)
        _db.session.commit()
        _post(as_owner, page=f"/partners/{c.id}")
        body = as_owner.get(f"/partners/{c.id}").get_data(as_text=True)
        assert body.count("主担当の欄で迷いました") == 1     # ひとことの欄だけ


class Test囲んだ場所:
    def test_四角ごと残る(self, as_owner, app):
        """**どこを囲んだかを残す。**場所が分からないと直しようがない"""
        as_owner.post("/notes", json={"body": "この2つは並べて見たい",
                                      "page": "/partners", "page_title": "顧問先",
                                      "quote": "未設定　期限が未記入",
                                      "rect": "0.3800,0.1000,0.1400,0.0700"})
        with app.app_context():
            n = _Note().query.one()
            assert n.rect == "0.3800,0.1000,0.1400,0.0700"
            assert n.quote == "未設定　期限が未記入"

    def test_画面を飛ばさずに返す(self, as_owner):
        """**書いたものを失わせない。**やり直しになるのが一番こたえる"""
        r = as_owner.post("/notes", json={"body": "見出しが小さい", "page": "/partners"})
        assert r.status_code == 200 and r.get_json()["ok"] is True

    def test_空はそのまま返す(self, as_owner):
        r = as_owner.post("/notes", json={"body": "  ", "page": "/partners"})
        assert r.status_code == 400 and r.get_json()["ok"] is False

    def test_描き戻せる形で渡す(self, as_owner):
        as_owner.post("/notes", json={"body": "ここ", "page": "/partners",
                                      "rect": "0.1,0.1,0.2,0.2"})
        body = as_owner.get("/partners").get_data(as_text=True)
        assert '"rect": "0.1,0.1,0.2,0.2"' in body


class Test最近の変更:
    """**当社の作業まで並ぶので、先方の画面には出さない。**

    「見え方の確認」「利用者の追加」まで混ざり、本題が見えなくなる。
    顧問先ごとの履歴は、その顧問先の画面に出ている。
    """

    def test_管理者には出さない(self, as_owner):
        assert "最近の変更" not in as_owner.get("/").get_data(as_text=True)

    def test_マスターには出る(self, as_master):
        assert "最近の変更" in as_master.get("/").get_data(as_text=True)

    def test_確認モード中は出ない(self, as_master):
        as_master.post("/preview/owner")
        assert "最近の変更" not in as_master.get("/").get_data(as_text=True)

    def test_顧問先ごとの履歴は残す(self, as_owner, app):
        """**全体の一覧を消しただけ。**個別の履歴まで消さない"""
        from app.models import Partner
        with app.app_context():
            c = Partner(name="見本商事")
            _db.session.add(c)
            _db.session.commit()
            cid = c.id
        assert "変更履歴" in as_owner.get(f"/partners/{cid}").get_data(as_text=True)


class Test辿りつけるか:
    """**押せる場所が分からなければ、無いのと同じ。**"""

    def test_サイドから開ける(self, as_owner):
        assert "届いたひとこと" in as_owner.get("/").get_data(as_text=True)

    def test_未対応の件数が出る(self, as_owner):
        _post(as_owner)
        body = as_owner.get("/").get_data(as_text=True)
        assert '<span class="badge warn">1</span>' in body

    def test_担当者には出さない(self, as_staff):
        assert "届いたひとこと" not in as_staff.get("/").get_data(as_text=True)
