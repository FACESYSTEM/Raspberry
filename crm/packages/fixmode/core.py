"""修正モードの本体。**表・経路・画面への差し込みだけ。**"""
import json
import warnings
import weakref
from datetime import datetime, timezone
from functools import wraps

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template,
                   request, url_for)

# 状態。**直す人と、確認する人が違う。**
#   open   届いた（作り手の出番）
#   fixed  直した（使い手の確認待ち）
#   done   確認できた（おしまい）
# 1つのボタンで閉じると、**直したつもりが直っていない**が通ってしまう。
STATES = ("open", "fixed", "done")
STATE_LABEL = {"open": "直していない", "fixed": "確認待ち", "done": "確認できた"}


def _utcnow() -> datetime:
    """**UTCで持つ。**表示するときだけその土地の時刻に直す。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class FixMode:
    """アプリに修正モードを足す。

    `db` は Flask-SQLAlchemy の `SQLAlchemy()`。
    表は `fixmode_notes` ひとつだけ。**利用者の表と繋がない**ので、
    持っていく先の作りが違っても、そのまま動く。
    """

    def __init__(self, db, model_name="Note"):
        self.db = db
        self.Note = _make_model(db, model_name)
        self.bp = None

    def init_app(self, app, *, can_comment, can_fix, can_close, who,
                 url_prefix="/notes", login_required=None,
                 base_template="fixmode/base.html"):
        """組み込む。

        can_comment()  書ける人か（作り手・使い手の両方）
        can_fix()      「直しました」と言える人か（**作り手だけ**）
        can_close()    「直っています」と閉じられる人か（**使い手だけ**）
        who()          いまの人 → (id, 表示名)
        login_required 経路に噛ませる飾り。無ければ素通し
        base_template  一覧を載せる土台の画面。無ければ自前の枠で出す
        """
        # **直す人と確認する人が同じでは、この部品の意味が半分になる。**
        # 「直したつもりが直っていない」を止められるのは、
        # 直した本人以外が確認するからで、そこを崩すと元の木阿弥になる。
        # 止めはしない（小さな組では兼ねることもある）が、黙ってもいない
        if can_fix is can_close:
            warnings.warn(
                "fixmode: can_fix と can_close に同じものが渡されています。"
                "直した本人が閉じられると、直したつもりが直っていないまま"
                "通ってしまいます。別の判定を渡してください。",
                stacklevel=2)
            app.logger.warning(
                "fixmode: can_fix と can_close が同じです（直した本人が閉じられます）")

        Note = self.Note
        guard = login_required or (lambda f: f)

        bp = Blueprint("fixmode", __name__,
                       template_folder="templates",
                       static_folder="static",
                       static_url_path="/fixmode-static")

        def need(check):
            def deco(fn):
                @wraps(fn)
                def inner(*a, **kw):
                    if not check():
                        abort(403)
                    return fn(*a, **kw)
                return inner
            return deco

        @bp.route("", methods=["POST"])
        @guard
        @need(can_comment)
        def add():
            """気づいたことを書き残す。**画面を飛ばさずに返す。**"""
            f = request.get_json(silent=True) or request.form
            body = (f.get("body") or "").strip()
            if not body:
                return jsonify({"ok": False,
                                "message": "ひとことを書いてから送ってください。"}), 400
            uid, name = who()
            n = Note(page=(f.get("page") or "")[:200],
                     page_title=(f.get("page_title") or "")[:120],
                     quote=(f.get("quote") or "")[:1000] or None,
                     rect=(f.get("rect") or "")[:60] or None,
                     body=body[:2000], author_id=uid, author_name=name)
            self.db.session.add(n)
            self.db.session.commit()
            return jsonify({"ok": True, "note": n.as_dict()})

        @bp.route("")
        @guard
        @need(lambda: can_fix() or can_close())
        def index():
            rows = Note.query.order_by(Note.created_at.desc()).all()
            return render_template(
                "fixmode/list.html",
                waiting=[n for n in rows if n.state == "fixed"],
                todo=[n for n in rows if n.state == "open"],
                done=[n for n in rows if n.state == "done"],
                can_fix=can_fix(), can_close=can_close(),
                fixmode_base=base_template)

        @bp.route("/<int:nid>/fixed", methods=["POST"])
        @guard
        @need(can_fix)
        def fixed(nid):
            """直した、と伝える。**閉じはしない。**"""
            n = self.db.session.get(Note, nid) or abort(404)
            f = request.get_json(silent=True) or request.form
            _, name = who()
            n.state = "fixed"
            n.fixed_at = _utcnow()
            n.fixed_by_name = name
            n.fixed_note = (f.get("fixed_note") or "").strip()[:1000] or None
            n.reopen_note = None
            self.db.session.commit()
            return _back("直したことをお伝えしました。確認待ちです。")

        @bp.route("/<int:nid>/accept", methods=["POST"])
        @guard
        @need(can_close)
        def accept(nid):
            """直っていることを確認できた。**ここで初めて閉じる。**"""
            n = self.db.session.get(Note, nid) or abort(404)
            _, name = who()
            n.state = "done"
            n.done_at = _utcnow()
            n.done_by_name = name
            self.db.session.commit()
            return _back("確認できたものとして閉じました。")

        @bp.route("/<int:nid>/reopen", methods=["POST"])
        @guard
        @need(can_close)
        def reopen(nid):
            """まだ直っていない、と差し戻す。**直した記録は消さない。**"""
            n = self.db.session.get(Note, nid) or abort(404)
            f = request.get_json(silent=True) or request.form
            n.state = "open"
            n.done_at = None
            n.done_by_name = None
            n.reopen_note = (f.get("reopen_note") or "").strip()[:1000] or None
            self.db.session.commit()
            return _back("差し戻しました。")

        def _back(msg):
            if request.is_json:
                return jsonify({"ok": True})
            flash(msg)
            return redirect(url_for("fixmode.index"))

        app.register_blueprint(bp, url_prefix=url_prefix)
        # **表に手が届くようにしておく。**試験や移行で使う
        app.extensions["fixmode"] = self
        self.bp = bp

        @app.context_processor
        def _inject():
            """画面に配る。**呼ばれたときだけ数える。**"""
            def notes_here():
                try:
                    return Note.open_on(request.path)
                except Exception:               # noqa: BLE001（表が無くても画面を出す）
                    return []

            def notes_here_json():
                return json.dumps([n.as_dict() for n in notes_here()],
                                  ensure_ascii=False)

            def open_count():
                try:
                    return Note.query.filter(Note.state != "done").count()
                except Exception:               # noqa: BLE001
                    return 0

            on = False
            try:
                on = can_comment()
            except Exception:                   # noqa: BLE001
                pass
            return {"FIXMODE_ON": on,
                    "FIXMODE_CAN_CLOSE": can_close() if on else False,
                    "FIXMODE_CAN_FIX": can_fix() if on else False,
                    "FIXMODE_OPEN": open_count() if on else 0,
                    "fixmode_notes_here": notes_here,
                    "fixmode_notes_json": notes_here_json,
                    "FIXMODE_STATE_LABEL": STATE_LABEL}

        return self


# **表の定義は1度だけ。**アプリを作り直すたびに定義すると、
# 同じ表を二重に登録して落ちる（試験で何度も作り直すときに出る）。
#
# 鍵は `db` そのもの。**`id()` を鍵にしない。**
# 片づけられたあとに同じ番号が別のものに割り当たると、
# 違うDBに同じ表を返してしまう。まず起きないが、起きたときに気づけない
_MODELS = weakref.WeakKeyDictionary()


def _make_model(db, name):
    made = _MODELS.setdefault(db, {})
    if name in made:
        return made[name]

    class Note(db.Model):
        """画面へのひとこと。

        **利用者の表と繋がない。**名前は書いた時点で写して持つ。
        持っていく先で利用者の作りが違っても、そのまま動くようにするため。
        """
        __tablename__ = "fixmode_notes"

        id = db.Column(db.Integer, primary_key=True)
        # どの画面か。**URLで持つ。**画面の名前は変わるが、URLは残りやすい
        page = db.Column(db.String(200), nullable=False, index=True)
        page_title = db.Column(db.String(120))
        # 囲んだ四角。**画面の幅・高さに対する割合。**画素で持つと端末でずれる
        rect = db.Column(db.String(60))
        # 囲んだ中にあった文字。**次に開くときの手がかり。**
        # 画面の作りが変われば割合は合わなくなるので、文字から探し直す
        quote = db.Column(db.Text)
        body = db.Column(db.Text, nullable=False)

        state = db.Column(db.String(10), nullable=False, default="open",
                          index=True)
        author_id = db.Column(db.Integer)
        author_name = db.Column(db.String(120))
        created_at = db.Column(db.DateTime, default=_utcnow, index=True)

        fixed_at = db.Column(db.DateTime)
        fixed_by_name = db.Column(db.String(120))
        fixed_note = db.Column(db.Text)          # 何を直したか
        reopen_note = db.Column(db.Text)         # どこが直っていないか
        done_at = db.Column(db.DateTime)
        done_by_name = db.Column(db.String(120))

        @staticmethod
        def open_on(path):
            """その画面の、まだ終わっていないもの。

            **直したものも出し続ける。**確認するには、その場所が要る。
            """
            return (Note.query
                    .filter(Note.page == path, Note.state != "done")
                    .order_by(Note.created_at.desc()).all())

        def as_dict(self):
            return {"id": self.id, "rect": self.rect, "body": self.body,
                    "quote": self.quote or "", "state": self.state,
                    "fixed_note": self.fixed_note or "",
                    "who": self.author_name or "",
                    "at": self.created_at.isoformat() if self.created_at else ""}

    Note.__name__ = name
    made[name] = Note
    return Note
