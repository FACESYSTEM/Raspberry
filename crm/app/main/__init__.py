"""ダッシュボードと、ドメインの**見本**（取引先）。

一覧・詳細・インライン編集・要確認の作法をここで見せる。業務に合わせて
作り直す前提なので、そのまま残さない。
"""

from flask import (Blueprint, abort, flash, jsonify, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required

from datetime import date

from ..timeutil import utcnow
from ..extensions import db
from ..models import ChangeHistory, Comment, Partner
from ..security import can_edit, is_master, scope_query

bp = Blueprint("main", __name__)

# 一覧の並び替えは**ホワイトリスト**で受ける。範囲外は無視（エラーにしない）
SORT_KEYS = ("code", "name", "kind", "contact_name", "created_at")
# インライン編集で書き換えてよい項目。**ここに無いものは400**
EDITABLE = {"code", "name", "kind", "contact_name", "contact_email",
            "tel", "address", "note"}
PER_PAGE = 50


@bp.route("/")
@login_required
def dashboard():
    """最初の画面。**今日気にすべきことを、探さずに出す。**

    探しに行かないと分からない形にすると、見られなくなる
    （実案件では、台帳の「ステータス」列が全件同じ値のまま死んでいた）。
    **合計だけでなく中身を出す**（土台 §4.7）。

    ここは見本です。業務が決まったら、**その業務で朝いちばんに見たいもの**に
    置き換えてください。
    """
    rows = scope_query(db.session.query(Partner), Partner).all()
    live = [p for p in rows if not p.archived_at]
    # **汚れているものを消さず、「要確認」として出す**（土台 §4.1）
    issues = [p for p in live if p.issues]
    return render_template(
        "dashboard.html", total=len(live), issues=issues,
        # **マスターのときだけ引く。**画面に出ないものを毎回数えない
        recent=(ChangeHistory.query.order_by(ChangeHistory.created_at.desc())
                .limit(10).all() if is_master() else []))


@bp.route("/partners")
@login_required
def partners():
    """一覧。**クエリの名前を全画面で揃える**（q / sort / dir / page）。"""
    q = (request.args.get("q") or "").strip()
    sort = request.args.get("sort")
    sort = sort if sort in SORT_KEYS else "code"
    desc = (request.args.get("dir") or "asc") == "desc"
    try:
        page = max(1, int(request.args.get("page") or 1))
    except ValueError:
        page = 1

    rows = scope_query(db.session.query(Partner), Partner).all()
    show_archived = request.args.get("archived") == "1"
    archived = sum(1 for p in rows if p.archived_at)
    if not show_archived:
        rows = [p for p in rows if not p.archived_at]
    if q:
        rows = [p for p in rows
                if q in (p.name or "") or q in (p.code or "")
                or q in (p.contact_name or "")]
    if request.args.get("issues") == "1":
        rows = [p for p in rows if p.issues]

    # **未採番（None）を落とさない。**空を最後に寄せる
    rows.sort(key=lambda p: (getattr(p, sort) is None,
                             str(getattr(p, sort) or "")), reverse=desc)
    total = len(rows)
    rows = rows[(page - 1) * PER_PAGE:page * PER_PAGE]
    return render_template("partners_list.html", rows=rows, q=q, sort=sort,
                           dir=request.args.get("dir") or "asc", page=page,
                           total=total, per_page=PER_PAGE, archived=archived,
                           show_archived=show_archived)


@bp.route("/partners/<int:pid>")
@login_required
def partner_detail(pid):
    p = scope_query(db.session.query(Partner), Partner).filter_by(id=pid).first()
    if p is None:
        abort(404)
    return render_template(
        "partner_detail.html", p=p,
        history=ChangeHistory.query.filter_by(entity_type="partner",
                                              entity_id=pid)
        .order_by(ChangeHistory.created_at.desc()).limit(30).all(),
        comments=Comment.query.filter_by(entity_type="partner", entity_id=pid)
        .order_by(Comment.created_at.desc()).all())


@bp.route("/partners/new", methods=["POST"])
@login_required
def partner_new():
    if not can_edit():
        abort(403)
    name = (request.form.get("name") or "").strip()
    if not name:
        flash("名前を入れてください。")
        return redirect(url_for("main.partners"))
    p = Partner(name=name, code=(request.form.get("code") or "").strip() or None)
    db.session.add(p)
    db.session.flush()
    ChangeHistory.record("partner", p.id, "追加", "", name, current_user.id)
    db.session.commit()
    return redirect(url_for("main.partner_detail", pid=p.id))


@bp.route("/api/partners/<int:pid>", methods=["PATCH"])
@login_required
def partner_patch(pid):
    """インライン編集。**必ず履歴を残してから保存する。**"""
    if not can_edit():
        return jsonify(ok=False, error="forbidden",
                       message="編集する権限がありません"), 403
    p = db.session.get(Partner, pid)
    if p is None:
        return jsonify(ok=False, error="not_found", message="ありません"), 404

    data = request.get_json(silent=True) or {}
    field = data.get("field")
    value = str(data.get("value") or "").strip()
    if field not in EDITABLE:
        return jsonify(ok=False, error="validation",
                       message="編集できない項目です"), 400
    if field == "name" and not value:
        return jsonify(ok=False, error="validation",
                       message="名前は空にできません"), 400

    old = getattr(p, field)
    if (old or "") == value:
        return jsonify(ok=True, value=value, history_id=None)
    setattr(p, field, value or None)
    h = ChangeHistory.record("partner", p.id, field, old, value,
                             current_user.id)
    db.session.commit()
    return jsonify(ok=True, value=value, history_id=h.id)


@bp.route("/partners/<int:pid>/archive", methods=["POST"])
@login_required
def partner_archive(pid):
    """**消さずに下げる。**履歴と集計に要る。"""
    if not can_edit():
        abort(403)
    p = db.session.get(Partner, pid) or abort(404)
    back = p.archived_at is not None
    p.archived_at = None if back else utcnow()
    ChangeHistory.record("partner", p.id, "アーカイブ",
                         "下げていた" if back else "台帳にあった",
                         "戻した" if back else "下げた", current_user.id)
    db.session.commit()
    flash("台帳に戻しました。" if back else "台帳から下げました。")
    return redirect(url_for("main.partner_detail", pid=pid))


@bp.route("/comments", methods=["POST"])
@login_required
def comment_new():
    body = (request.form.get("body") or "").strip()
    pid = int(request.form.get("entity_id") or 0)
    if body:
        db.session.add(Comment(entity_type="partner", entity_id=pid,
                               body=body, author_user_id=current_user.id))
        db.session.commit()
    return redirect(url_for("main.partner_detail", pid=pid))
