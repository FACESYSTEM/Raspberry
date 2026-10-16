"""ダッシュボードと業務の画面（ゲスト・キャスト）。言葉は規約 §7。

一覧・詳細・インライン編集・要確認・消さずに下げる、の作法はひな形の見本
（取引先）から引き継いでいる。
"""
from collections import Counter
from datetime import date, datetime, timedelta

from flask import (Blueprint, abort, flash, jsonify, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload, selectinload

from ..extensions import db
from ..models import (Assignment, Bottle, Cast, ChangeHistory, Choice,
                      Comment, Corp, Guest, Visit)
from ..security import (can_edit, feature_required, is_master, is_owner,
                        role_required, scope_query)
from ..timeutil import to_jst, utcnow

bp = Blueprint("main", __name__)

PER_PAGE = 50

# 一覧の並び替えは**ホワイトリスト**で受ける。範囲外は無視（エラーにしない）
GUEST_SORT = ("guest_no", "name", "kana", "last_visit", "created_at")

# インライン編集で書き換えてよい項目と、その読み方。**ここに無いものは400**
GUEST_TEXT = {"name", "kana", "title", "neck_name", "tel", "email",
              "address", "note"}
GUEST_INT = {"guest_no", "birth_year", "birth_month", "birth_day"}
GUEST_DATE = {"registered_on"}
GUEST_CORP = {"corp", "corp_kana"}
GUEST_EDITABLE = GUEST_TEXT | GUEST_INT | GUEST_DATE | GUEST_CORP

CAST_TEXT = {"name", "note"}
CAST_INT = {"cast_no"}
CAST_EDITABLE = CAST_TEXT | CAST_INT

# 履歴に出す項目名。**コード名のまま出さない**（読む人は店の人）
FIELD_LABEL = {
    "guest_no": "ゲスト番号", "name": "名前", "kana": "フリガナ",
    "corp": "法人・団体", "corp_kana": "法人・団体のフリガナ", "title": "役職",
    "neck_name": "ネック名", "birth_year": "誕生日（年）",
    "birth_month": "誕生日（月）", "birth_day": "誕生日（日）", "tel": "電話",
    "email": "メール", "address": "住所", "note": "備考",
    "registered_on": "登録日", "cast_no": "キャスト番号",
}


def today_jst() -> date:
    """店の「今日」。**サーバーの時計（UTC）で日付を出すと、朝9時まで前の日になる。**"""
    return to_jst(utcnow()).date()


def _guests():
    """ゲストを引く口。**必ず `scope_query` を通す**（規約 §5）。

    担当・来店・ボトル・法人は一覧でも使うので先にまとめて読む。
    ゲストごとに引くと、2,000件の一覧で何千回も問い合わせることになる。
    """
    q = db.session.query(Guest).options(
        joinedload(Guest.corp),
        selectinload(Guest.assignments).joinedload(Assignment.cast),
        selectinload(Guest.visits),
        selectinload(Guest.bottles))
    return scope_query(q, Guest)


def _guest_or_404(gid: int) -> Guest:
    got = _guests().filter(Guest.id == gid).first()
    if got is None:
        abort(404)
    return got


def _err(message: str, code: int = 400, error: str = "validation"):
    return jsonify(ok=False, error=error, message=message), code


def _page() -> int:
    try:
        return max(1, int(request.args.get("page") or 1))
    except ValueError:
        return 1


# ---------------------------------------------------------------- 値の読み方

def parse_int(field: str, raw: str):
    """数字の項目を読む。**空は「分からない」として None。**

    範囲外は保存しない。誕生日の月に 13 が入ると、誕生月の一覧から黙って漏れる。
    """
    raw = raw.strip().replace("Gu_", "").replace("CA_", "")
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError("数字で入れてください")
    v = int(raw)
    if field == "birth_month" and not 1 <= v <= 12:
        raise ValueError("月は1〜12で入れてください")
    if field == "birth_day" and not 1 <= v <= 31:
        raise ValueError("日は1〜31で入れてください")
    if field == "birth_year" and not 1900 <= v <= today_jst().year:
        raise ValueError(f"年は1900〜{today_jst().year}で入れてください")
    if field in ("guest_no", "cast_no") and v < 1:
        raise ValueError("番号は1以上で入れてください")
    return v


def parse_date(raw: str):
    raw = raw.strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw.replace("/", "-"), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("日付は 2026-10-16 の形で入れてください") from None


def _birthday_ok(g: Guest) -> bool:
    """月と日の組み合わせが暦にあるか。**年が分からなければ2/29も通す。**"""
    if not (g.birth_month and g.birth_day):
        return True
    try:
        date(g.birth_year or 2000, g.birth_month, g.birth_day)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------- ダッシュボード

@bp.route("/")
@login_required
def dashboard():
    """最初の画面。**今日気にすべきことを、探さずに出す。**

    誕生月のゲスト・最近の来店・要確認の内訳。**合計だけでなく中身を出す**
    （規約 §12）。要確認は種類ごとに数え、押すとその一覧に飛ぶ。
    """
    rows = [x for x in _guests().all() if not x.archived_at]
    issue_count = Counter(i for x in rows for i in x.issues)
    t = today_jst()
    nxt = 1 if t.month == 12 else t.month + 1
    births = sorted((x for x in rows if x.birth_month in (t.month, nxt)),
                    key=lambda x: ((x.birth_month - t.month) % 12,
                                   x.birth_day or 99))
    since = t - timedelta(days=14)
    visits = sorted(((v, x) for x in rows for v in x.live_visits
                     if v.visited_on >= since),
                    key=lambda p: p[0].visited_on, reverse=True)
    return render_template(
        "dashboard.html", total=len(rows),
        issue_total=sum(1 for x in rows if x.issues),
        issue_count=issue_count.most_common(), births=births,
        month=t.month, next_month=nxt, visits=visits[:50],
        visit_total=len(visits), since=since,
        # **マスターのときだけ引く。**画面に出ないものを毎回数えない
        recent=(ChangeHistory.query.order_by(ChangeHistory.created_at.desc())
                .limit(10).all() if is_master() else []))


# ---------------------------------------------------------------- ゲスト

def _matches(x: Guest, q: str) -> bool:
    """探す。**名前・フリガナ・番号・法人・ネック名・ボトル・担当キャスト**。"""
    no = q.replace("Gu_", "").replace("Gu", "").strip()
    if no.isdigit() and x.guest_no == int(no):
        return True
    hay = [x.name, x.kana, x.neck_name, x.note,
           x.corp.name if x.corp else None, x.corp.kana if x.corp else None]
    hay += [b.label for b in x.bottles]
    hay += [a.cast.name for a in x.active_assignments if a.cast]
    return any(q in (h or "") for h in hay)


def _sort_value(x: Guest, sort: str):
    """並び替えの値。**空は None で返す**（呼び側で最後に寄せる）。"""
    if sort == "last_visit":
        return x.last_visit_on
    v = getattr(x, sort)
    if v is None or sort in ("guest_no", "created_at"):
        return v
    return str(v) or None


def sort_rows(rows: list, sort: str, desc: bool) -> list:
    """並べる。**空は向きにかかわらず最後。**

    空を「最後に寄せるキー」で並べてから逆順にすると、降順のときだけ
    空が先頭に来る。番号なし・来店の記録なしが一覧の頭を埋めてしまう。
    """
    has = [x for x in rows if _sort_value(x, sort) is not None]
    empty = [x for x in rows if _sort_value(x, sort) is None]
    has.sort(key=lambda x: _sort_value(x, sort), reverse=desc)
    return has + empty


@bp.route("/guests")
@login_required
@feature_required("guests")
def guests():
    """一覧。**クエリの名前を全画面で揃える**（q / sort / dir / page）。"""
    q = (request.args.get("q") or "").strip()
    sort = request.args.get("sort")
    sort = sort if sort in GUEST_SORT else "guest_no"
    dir_ = "desc" if request.args.get("dir") == "desc" else "asc"
    page = _page()

    rows = _guests().all()
    show_archived = request.args.get("archived") == "1"
    archived = sum(1 for x in rows if x.archived_at)
    rows = [x for x in rows if bool(x.archived_at) == show_archived]
    if q:
        rows = [x for x in rows if _matches(x, q)]
    # 要確認は**種類でも絞れる**（ダッシュボードの内訳から飛んでくる）
    issue = (request.args.get("issue") or "").strip()
    if issue:
        rows = [x for x in rows if issue in x.issues]
    elif request.args.get("issues") == "1":
        rows = [x for x in rows if x.issues]

    rows = sort_rows(rows, sort, dir_ == "desc")
    total = len(rows)
    rows = rows[(page - 1) * PER_PAGE:page * PER_PAGE]
    return render_template("guests_list.html", rows=rows, q=q, sort=sort,
                           dir=dir_, page=page, total=total,
                           per_page=PER_PAGE, archived=archived,
                           show_archived=show_archived, issue=issue)


@bp.route("/guests/<int:gid>")
@login_required
@feature_required("guests")
def guest_detail(gid):
    x = _guest_or_404(gid)
    casts = (Cast.query.filter(Cast.archived_at.is_(None))
             .order_by(Cast.cast_no.is_(None), Cast.cast_no, Cast.name).all())
    return render_template(
        "guest_detail.html", x=x, casts=casts,
        levels=Choice.options("level"), today=today_jst(),
        FIELD_LABEL=FIELD_LABEL,
        history=ChangeHistory.query.filter_by(entity_type="guest",
                                              entity_id=gid)
        .order_by(ChangeHistory.created_at.desc()).limit(50).all(),
        comments=Comment.query.filter_by(entity_type="guest", entity_id=gid)
        .order_by(Comment.created_at.desc()).all())


@bp.route("/guests/new", methods=["POST"])
@login_required
@feature_required("guests")
def guest_new():
    """追加。**番号も名前も空で登録できる。**分かったところから埋める。"""
    if not can_edit():
        abort(403)
    name = (request.form.get("name") or "").strip() or None
    try:
        no = parse_int("guest_no", request.form.get("guest_no") or "")
    except ValueError as e:
        flash(f"ゲスト番号：{e}")
        return redirect(url_for("main.guests"))
    if not name and no is None:
        flash("番号か名前のどちらかを入れてください。")
        return redirect(url_for("main.guests"))
    x = Guest(name=name, guest_no=no)
    db.session.add(x)
    db.session.flush()
    ChangeHistory.record("guest", x.id, "追加", "", x.label, current_user.id)
    db.session.commit()
    if no is not None and Guest.query.filter_by(guest_no=no).count() > 1:
        # **止めずに知らせる。**重複は実在するので弾かない（規約 §7.1）
        flash(f"Gu_{no} は他のゲストにも付いています。要確認に出ます。")
    return redirect(url_for("main.guest_detail", gid=x.id))


def _set_corp(x: Guest, field: str, value: str):
    """法人・団体を名前で付け替える。**同じ名前があればそれに寄せる。**

    表記ゆれ（㈱／株式会社）は寄せない。同じ会社かは人が決める。
    """
    if field == "corp_kana":
        if x.corp is None:
            raise ValueError("先に法人・団体の名前を入れてください")
        old = x.corp.kana
        x.corp.kana = value or None
        ChangeHistory.record("corp", x.corp.id, "フリガナ", old, value,
                             current_user.id)
        return old
    old = x.corp.name if x.corp else None
    if not value:
        x.corp = None
        return old
    got = (Corp.query.filter(Corp.name == value, Corp.archived_at.is_(None))
           .order_by(Corp.id).first())
    if got is None:
        got = Corp(name=value)
        db.session.add(got)
    x.corp = got
    return old


@bp.route("/api/guests/<int:gid>", methods=["PATCH"])
@login_required
@feature_required("guests")
def guest_patch(gid):
    """インライン編集。**必ず履歴を残してから保存する。**"""
    if not can_edit():
        return _err("編集する権限がありません", 403, "forbidden")
    x = _guests().filter(Guest.id == gid).first()
    if x is None:
        return _err("ありません", 404, "not_found")

    data = request.get_json(silent=True) or {}
    field = data.get("field")
    raw = str(data.get("value") or "").strip()
    if field not in GUEST_EDITABLE:
        return _err("編集できない項目です")

    try:
        if field in GUEST_CORP:
            old = _set_corp(x, field, raw)
            new = raw or None
        else:
            new = (parse_int(field, raw) if field in GUEST_INT
                   else parse_date(raw) if field in GUEST_DATE
                   else (raw or None))
            old = getattr(x, field)
            if old == new:
                return jsonify(ok=True, value=raw, history_id=None)
            setattr(x, field, new)
            if not _birthday_ok(x):
                db.session.rollback()
                return _err("その月にない日です")
    except ValueError as e:
        db.session.rollback()
        return _err(str(e))

    if (old or None) == (new or None):
        db.session.rollback()
        return jsonify(ok=True, value=raw, history_id=None)
    h = ChangeHistory.record("guest", x.id, FIELD_LABEL.get(field, field),
                             old, new, current_user.id)
    db.session.commit()
    shown = "" if new is None else str(new)
    return jsonify(ok=True, value=shown, history_id=h.id)


@bp.route("/guests/<int:gid>/archive", methods=["POST"])
@login_required
@feature_required("guests")
def guest_archive(gid):
    """**消さずに下げる。**来店とボトルの履歴が要る。"""
    if not can_edit():
        abort(403)
    x = _guest_or_404(gid)
    back = x.archived_at is not None
    x.archived_at = None if back else utcnow()
    ChangeHistory.record("guest", x.id, "一覧から下げる",
                         "下げていた" if back else "一覧にあった",
                         "戻した" if back else "下げた", current_user.id)
    db.session.commit()
    flash("一覧に戻しました。" if back else "一覧から下げました。")
    return redirect(url_for("main.guest_detail", gid=gid))


@bp.route("/guests/<int:gid>/no_cast", methods=["POST"])
@login_required
@feature_required("guests")
def guest_no_cast(gid):
    """「担当なし」を確かめた／取り消す。**分からないと区別する**（規約 §7.3）。"""
    if not can_edit():
        abort(403)
    x = _guest_or_404(gid)
    if x.active_assignments:
        flash("担当キャストがいるため「担当なし」にはできません。先に担当を外してください。")
        return redirect(url_for("main.guest_detail", gid=gid))
    x.no_cast = not x.no_cast
    ChangeHistory.record("guest", x.id, "担当",
                         "分からない" if x.no_cast else "担当なし",
                         "担当なし" if x.no_cast else "分からない",
                         current_user.id)
    db.session.commit()
    return redirect(url_for("main.guest_detail", gid=gid))


@bp.route("/guests/<int:gid>/assignments", methods=["POST"])
@login_required
@feature_required("guests")
def assignment_new(gid):
    if not can_edit():
        abort(403)
    x = _guest_or_404(gid)
    cid = request.form.get("cast_id") or ""
    c = db.session.get(Cast, int(cid)) if cid.isdigit() else None
    if c is None or c.archived_at:
        flash("キャストを選んでください。")
        return redirect(url_for("main.guest_detail", gid=gid))
    if any(a.cast_id == c.id for a in x.active_assignments):
        flash(f"{c.label} は既に担当です。")
        return redirect(url_for("main.guest_detail", gid=gid))
    level = (request.form.get("level") or "").strip() or None
    mark = (request.form.get("mark") or "").strip() or None
    db.session.add(Assignment(guest=x, cast=c, level=level, mark=mark))
    x.no_cast = False          # 担当がいるなら「担当なし」は外れる
    ChangeHistory.record("guest", x.id, "担当を付ける", "",
                         " ".join(v for v in (c.label, level, mark) if v),
                         current_user.id)
    db.session.commit()
    return redirect(url_for("main.guest_detail", gid=gid))


@bp.route("/assignments/<int:aid>/end", methods=["POST"])
@login_required
@feature_required("guests")
def assignment_end(aid):
    """担当を外す。**消さない**（誰がいつまで担当していたかを残す）。"""
    if not can_edit():
        abort(403)
    a = db.session.get(Assignment, aid) or abort(404)
    x = _guest_or_404(a.guest_id)
    if a.ended_at is None:
        a.ended_at = utcnow()
        ChangeHistory.record("guest", x.id, "担当を外す",
                             a.cast.label if a.cast else "", "",
                             current_user.id)
        db.session.commit()
    return redirect(url_for("main.guest_detail", gid=x.id))


@bp.route("/guests/<int:gid>/bottles", methods=["POST"])
@login_required
@feature_required("guests")
def bottle_new(gid):
    """ボトルを1本足す。**書かれたとおり（`label`）は必ず残す。**"""
    if not can_edit():
        abort(403)
    x = _guest_or_404(gid)
    label = (request.form.get("label") or "").strip()
    if not label:
        flash("ボトルを入れてください（例：マッカラン⑩P）。")
        return redirect(url_for("main.guest_detail", gid=gid))
    try:
        no = parse_int("keep_no", request.form.get("keep_no") or "")
    except ValueError as e:
        flash(f"ボトル番号：{e}")
        return redirect(url_for("main.guest_detail", gid=gid))
    b = Bottle(guest=x, label=label, keep_no=no,
               brand=(request.form.get("brand") or "").strip() or None,
               place=(request.form.get("place") or "").strip() or None)
    db.session.add(b)
    ChangeHistory.record("guest", x.id, "ボトルを足す", "", label,
                         current_user.id)
    db.session.commit()
    return redirect(url_for("main.guest_detail", gid=gid))


@bp.route("/bottles/<int:bid>/finish", methods=["POST"])
@login_required
@feature_required("guests")
def bottle_finish(bid):
    """飲み切り／戻す。**消さずに下げる。**"""
    if not can_edit():
        abort(403)
    b = db.session.get(Bottle, bid) or abort(404)
    x = _guest_or_404(b.guest_id)
    back = b.finished_at is not None
    b.finished_at = None if back else utcnow()
    ChangeHistory.record("guest", x.id, "ボトル", b.label,
                         "キープに戻した" if back else "飲み切り",
                         current_user.id)
    db.session.commit()
    return redirect(url_for("main.guest_detail", gid=x.id))


@bp.route("/guests/<int:gid>/visits", methods=["POST"])
@login_required
@feature_required("guests")
def visit_new(gid):
    if not can_edit():
        abort(403)
    x = _guest_or_404(gid)
    try:
        on = parse_date(request.form.get("visited_on") or "") or today_jst()
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("main.guest_detail", gid=gid))
    if on > today_jst():
        flash("先の日付の来店は入れられません。")
        return redirect(url_for("main.guest_detail", gid=gid))
    db.session.add(Visit(guest=x, visited_on=on,
                         note=(request.form.get("note") or "").strip() or None,
                         created_by_id=current_user.id))
    ChangeHistory.record("guest", x.id, "来店を記録", "", on.isoformat(),
                         current_user.id)
    db.session.commit()
    return redirect(url_for("main.guest_detail", gid=gid))


@bp.route("/visits/<int:vid>/cancel", methods=["POST"])
@login_required
@feature_required("guests")
def visit_cancel(vid):
    """打ち間違いの来店を取り消す／戻す。**消さない。**"""
    if not can_edit():
        abort(403)
    v = db.session.get(Visit, vid) or abort(404)
    x = _guest_or_404(v.guest_id)
    back = v.archived_at is not None
    v.archived_at = None if back else utcnow()
    ChangeHistory.record("guest", x.id, "来店",
                         v.visited_on.isoformat(),
                         "取り消しを戻した" if back else "取り消した",
                         current_user.id)
    db.session.commit()
    return redirect(url_for("main.guest_detail", gid=x.id))


@bp.route("/comments", methods=["POST"])
@login_required
@feature_required("guests")
def comment_new():
    """対応履歴。**見えないゲストには書かせない。**"""
    if not can_edit():
        abort(403)
    raw = request.form.get("entity_id") or ""
    x = _guest_or_404(int(raw) if raw.isdigit() else 0)
    body = (request.form.get("body") or "").strip()
    if body:
        db.session.add(Comment(entity_type="guest", entity_id=x.id,
                               body=body, author_user_id=current_user.id))
        db.session.commit()
    return redirect(url_for("main.guest_detail", gid=x.id))


# ---------------------------------------------------------------- キャスト

@bp.route("/casts")
@login_required
@feature_required("casts")
def casts():
    """キャストの一覧。**担当ゲスト数は数えて出す**（列で持たない）。"""
    q = scope_query(db.session.query(Cast).options(
        selectinload(Cast.assignments).joinedload(Assignment.guest)), Cast)
    show_archived = request.args.get("archived") == "1"
    rows = q.all()
    archived = sum(1 for c in rows if c.archived_at)
    rows = [c for c in rows if bool(c.archived_at) == show_archived]
    rows.sort(key=lambda c: (c.cast_no is None, c.cast_no or 0, c.name))
    return render_template("casts_list.html", rows=rows, archived=archived,
                           show_archived=show_archived)


@bp.route("/casts/<int:cid>")
@login_required
@feature_required("casts")
def cast_detail(cid):
    c = scope_query(db.session.query(Cast), Cast).filter(Cast.id == cid).first()
    if c is None:
        abort(404)
    guests = sorted(c.active_assignments,
                    key=lambda a: (a.guest.guest_no is None,
                                   a.guest.guest_no or 0))
    return render_template(
        "cast_detail.html", c=c, rows=guests, FIELD_LABEL=FIELD_LABEL,
        history=ChangeHistory.query.filter_by(entity_type="cast",
                                              entity_id=cid)
        .order_by(ChangeHistory.created_at.desc()).limit(30).all())


@bp.route("/casts/new", methods=["POST"])
@login_required
@role_required("owner")
@feature_required("casts")
def cast_new():
    """キャストを足す。**番号は空でもよい**（番号の無いキャストが実在する）。"""
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("名前を入れてください。")
        return redirect(url_for("main.casts"))
    try:
        no = parse_int("cast_no", request.form.get("cast_no") or "")
    except ValueError as e:
        flash(f"キャスト番号：{e}")
        return redirect(url_for("main.casts"))
    c = Cast(name=name, cast_no=no)
    db.session.add(c)
    db.session.flush()
    ChangeHistory.record("cast", c.id, "追加", "", c.label, current_user.id)
    db.session.commit()
    return redirect(url_for("main.cast_detail", cid=c.id))


@bp.route("/api/casts/<int:cid>", methods=["PATCH"])
@login_required
@feature_required("casts")
def cast_patch(cid):
    if not is_owner():
        return _err("キャストを直せるのは管理者です", 403, "forbidden")
    c = db.session.get(Cast, cid)
    if c is None:
        return _err("ありません", 404, "not_found")
    data = request.get_json(silent=True) or {}
    field = data.get("field")
    raw = str(data.get("value") or "").strip()
    if field not in CAST_EDITABLE:
        return _err("編集できない項目です")
    if field == "name" and not raw:
        return _err("名前は空にできません")
    try:
        new = parse_int(field, raw) if field in CAST_INT else (raw or None)
    except ValueError as e:
        return _err(str(e))
    old = getattr(c, field)
    if old == new:
        return jsonify(ok=True, value=raw, history_id=None)
    setattr(c, field, new)
    h = ChangeHistory.record("cast", c.id, FIELD_LABEL.get(field, field),
                             old, new, current_user.id)
    db.session.commit()
    return jsonify(ok=True, value="" if new is None else str(new),
                   history_id=h.id)


@bp.route("/casts/<int:cid>/archive", methods=["POST"])
@login_required
@role_required("owner")
@feature_required("casts")
def cast_archive(cid):
    """在籍から外す／戻す。**消さない**（担当の履歴が読めなくなる）。"""
    c = db.session.get(Cast, cid) or abort(404)
    back = c.archived_at is not None
    c.archived_at = None if back else utcnow()
    ChangeHistory.record("cast", c.id, "在籍",
                         "外していた" if back else "在籍",
                         "在籍に戻した" if back else "外した", current_user.id)
    db.session.commit()
    flash("在籍に戻しました。" if back else
          "在籍から外しました。担当の記録は残ります。")
    return redirect(url_for("main.cast_detail", cid=cid))
