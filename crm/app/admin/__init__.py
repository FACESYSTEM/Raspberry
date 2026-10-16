"""管理。利用者・組織の設定・外部に渡す鍵・保管したログイン情報。"""
import secrets

from flask import (Blueprint, abort, flash, jsonify, redirect,
                   render_template, request, url_for)
from flask_login import current_user, login_required
from werkzeug.security import check_password_hash, generate_password_hash

from .. import secretbox
from ..auth import invite_and_mail
from ..timeutil import utcnow
from ..extensions import db
from ..models import (CHOICE_GROUPS, FEATURE_LABEL, FEATURES, ApiKey,
                      ChangeHistory, Choice, Credential, Feature, OrgProfile,
                      Partner, User)
from ..security import (ROLES, STAFF_ROLES, capability_matrix,
                        feature_required, is_master, role_required,
                        visible_roles)

bp = Blueprint("admin", __name__)

KEY_PREFIX_LEN = 8


@bp.route("/admin")
@login_required
@role_required("owner")
def index():
    return render_template(
        "admin_index.html",
        # **マスター（当社）は先方の一覧に出さない。**
        # 開発側の人が混ざっていると、そこに説明が要る
        users=[u for u in User.query.order_by(User.created_at).all()
               if is_master() or u.role != "master"],
        roles=[r for r in ROLES if r != "master"],   # マスターは画面から選ばせない
        staff_roles=STAFF_ROLES,
        orgs=OrgProfile.all_companies(),
        # 社外の方を紐づける先。**見本では取引先**
        partners=Partner.query.filter(Partner.archived_at.is_(None))
        .order_by(Partner.name).all(),
        matrix=capability_matrix(visible_roles()),
        matrix_roles=visible_roles())


@bp.route("/admin/users", methods=["POST"])
@login_required
@role_required("owner")
def user_new():
    """招待する。**初期パスワードを人が決めない。**"""
    email = (request.form.get("email") or "").strip().lower()
    name = (request.form.get("name") or "").strip()
    role = request.form.get("role") or "staff"
    if not email or not name:
        flash("名前とメールアドレスを入れてください。")
        return redirect(url_for("admin.index"))
    if role not in ROLES:
        role = "staff"
    if User.query.filter_by(email=email).first():
        flash("そのメールアドレスは既にあります。")
        return redirect(url_for("admin.index"))

    # **社外の方は、どこに属するかを必ず選ばせる。**
    # 紐づけ忘れのまま作ると「役割は社外なのに、どこにも属さない人」ができる
    sid = (request.form.get("scope_id") or "").strip()
    scope_id = int(sid) if role == "outside" and sid.isdigit() else None
    if role == "outside" and scope_id is None:
        flash("社外の方を招待するときは、どちらの方かを選んでください。")
        return redirect(url_for("admin.index"))

    u = User(email=email, name=name, role=role, scope_partner_id=scope_id)
    db.session.add(u)
    db.session.flush()
    where = ""
    if scope_id:
        p = db.session.get(Partner, scope_id)
        where = f"／{p.name}" if p else ""
    ChangeHistory.record("user", u.id, "追加", "", f"{name}／{role}{where}",
                         current_user.id)
    db.session.commit()
    url = invite_and_mail(u)
    flash(f"招待しました。メールが届かない場合はこのリンクを渡してください：{url}")
    return redirect(url_for("admin.index"))


@bp.route("/admin/users/<int:uid>", methods=["POST"])
@login_required
@role_required("owner")
def user_update(uid):
    u = db.session.get(User, uid) or abort(404)
    # **マスターは一覧から隠しているだけでは足りない。**
    # 画面に出さなくても、URLを直に叩けば無効にできてしまう
    if u.role == "master" and not is_master():
        abort(404)
    act = request.form.get("action")

    if act == "invite":
        url = invite_and_mail(u)
        flash(f"招待し直しました：{url}")
    elif act == "toggle":
        # **最後の管理者を無効にしない。**入れる人がいなくなる
        if u.role == "owner" and u.is_active_user and _active_owners() <= 1:
            flash("管理者が1人になるため、この操作はできません。")
            return redirect(url_for("admin.index"))
        u.is_active_user = not u.is_active_user
        ChangeHistory.record("user", u.id, "利用",
                             "有効" if not u.is_active_user else "無効",
                             "無効" if not u.is_active_user else "有効",
                             current_user.id)
        db.session.commit()
    elif act == "email":
        # **打ち間違いは必ず起きる。**実案件で実際に起きた。
        # その都度こちらに依頼が要る形にしない
        new = (request.form.get("email") or "").strip().lower()
        if not new or "@" not in new:
            flash("メールアドレスの形になっていません。")
            return redirect(url_for("admin.index"))
        if new != u.email:
            other = User.query.filter_by(email=new).first()
            if other is not None:
                # **どの利用者と重なったかは出さない。**
                # 誰が登録されているかを調べる手がかりになる
                flash("そのメールアドレスは既に使われています。")
                return redirect(url_for("admin.index"))
            ChangeHistory.record("user", u.id, "メールアドレス", u.email, new,
                                 current_user.id)
            u.email = new
            db.session.commit()
            flash(f"{u.name} のメールアドレスを変えました。"
                  "次のログインから新しいほうをお使いいただきます。")
    elif act == "role":
        role = request.form.get("role")
        if role == "outside":
            # **社外に変えるときは紐づけが要る。**画面から個別に行う
            flash("社外の方への変更は、招待し直してください。")
            return redirect(url_for("admin.index"))
        if role in ROLES:
            if u.role == "owner" and role != "owner" and _active_owners() <= 1:
                flash("管理者が1人になるため、この操作はできません。")
                return redirect(url_for("admin.index"))
            ChangeHistory.record("user", u.id, "役割", u.role, role,
                                 current_user.id)
            u.role = role
            db.session.commit()
    return redirect(url_for("admin.index"))


def _active_owners() -> int:
    return User.query.filter_by(role="owner", is_active_user=True).count()


# ---------- 外部に渡す鍵 ----------

@bp.route("/admin/apikeys", methods=["GET", "POST"])
@login_required
@role_required("owner")
@feature_required("apikeys")
def apikeys():
    """発行して**1度だけ表示する**。平文は保存しない。"""
    plain = None
    if request.method == "POST":
        if request.form.get("action") == "revoke":
            row = db.session.get(ApiKey, int(request.form.get("id") or 0)) \
                or abort(404)
            row.revoked_at = utcnow()
            ChangeHistory.record("api_key", row.id, "失効", row.name, "",
                                 current_user.id)
            db.session.commit()
            flash(f"「{row.name}」を失効させました。")
            return redirect(url_for("admin.apikeys"))

        name = (request.form.get("name") or "").strip()
        if not name:
            flash("渡す先が分かる名前を入れてください。")
            return redirect(url_for("admin.apikeys"))
        prefix = secrets.token_hex(KEY_PREFIX_LEN // 2)
        plain = f"{prefix}_{secrets.token_urlsafe(32)}"
        row = ApiKey(name=name, scope=request.form.get("scope") or "default",
                     prefix=prefix, key_hash=generate_password_hash(plain),
                     note=request.form.get("note") or "")
        db.session.add(row)
        db.session.flush()
        ChangeHistory.record("api_key", row.id, "発行", "", name,
                             current_user.id)
        db.session.commit()

    return render_template("admin_apikeys.html", plain=plain,
                           rows=ApiKey.query.order_by(
                               ApiKey.revoked_at.isnot(None),
                               ApiKey.created_at.desc()).all())


def verify_api_key(plain: str, scope: str = "default") -> ApiKey | None:
    """外部から来た鍵を照合する。**用途で絞る。**"""
    if not plain or "_" not in plain:
        return None
    prefix = plain.split("_", 1)[0]
    for row in ApiKey.query.filter_by(prefix=prefix, scope=scope).all():
        if row.alive and check_password_hash(row.key_hash, plain):
            row.last_used_at = utcnow()
            db.session.commit()
            return row
    return None


# ---------- 保管したログイン情報 ----------

@bp.route("/admin/credentials", methods=["GET", "POST"])
@login_required
@role_required("owner")
@feature_required("credentials")
def credentials():
    """**一覧に平文を出さない。**押した1件だけ返す（`credential_reveal`）。"""
    if request.method == "POST":
        if request.form.get("action") == "delete":
            row = db.session.get(Credential, int(request.form.get("id") or 0)) \
                or abort(404)
            ChangeHistory.record("credential", row.id, "削除",
                                 f"{row.group or ''} {row.name}".strip(), "",
                                 current_user.id)
            db.session.delete(row)
            db.session.commit()
            return redirect(url_for("admin.credentials"))

        if not secretbox.ready():
            flash("CREDENTIAL_KEY が未設定なので保存できません。"
                  "平文で置かないため、鍵を入れてから保存してください。")
            return redirect(url_for("admin.credentials"))

        cid = int(request.form.get("id") or 0)
        row = (db.session.get(Credential, cid) or abort(404)) if cid \
            else Credential()
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("名前を入れてください。")
            return redirect(url_for("admin.credentials"))
        row.group = (request.form.get("group") or "").strip()[:40]
        row.name = name[:120]
        row.url = (request.form.get("url") or "").strip()[:300]
        row.login_id = (request.form.get("login_id") or "").strip()[:200]
        row.note = (request.form.get("note") or "").strip()
        row.updated_by_id = current_user.id
        # **空なら変えない。**編集のたびに打ち直させるとかえって危ない
        pw = request.form.get("password") or ""
        if pw:
            row.password_enc = secretbox.seal(pw)
        db.session.add(row)
        db.session.flush()
        ChangeHistory.record("credential", row.id,
                             "更新" if cid else "追加", "",
                             f"{row.group or ''} {row.name}".strip(),
                             current_user.id)
        if pw:
            # **値は残さない。**履歴から漏れては意味がない
            ChangeHistory.record("credential", row.id, "パスワード",
                                 "（伏せ字）", "（伏せ字）", current_user.id)
        db.session.commit()
        flash(f"「{row.name}」を保存しました。")
        return redirect(url_for("admin.credentials"))

    rows = Credential.query.order_by(Credential.group, Credential.name).all()
    return render_template("admin_credentials.html", rows=rows,
                           ready=secretbox.ready(),
                           groups=sorted({r.group for r in rows if r.group}))


@bp.route("/admin/credentials/<int:cid>/reveal", methods=["POST"])
@login_required
@role_required("owner")
@feature_required("credentials")
def credential_reveal(cid):
    """1件だけ返す。**誰がいつ見たかを残す。**"""
    row = db.session.get(Credential, cid) or abort(404)
    ChangeHistory.record("credential", row.id, "パスワードを表示", "", "",
                         current_user.id)
    db.session.commit()
    return jsonify(ok=True, value=secretbox.open_(row.password_enc))


@bp.route("/admin/org")
@login_required
@role_required("owner")
@feature_required("org")
def org():
    """自社の情報。**一覧から切り離した別ページ。**

    一度決めたらほとんど触らない。毎日見る画面に混ぜると、
    **探すものが1つ増えるだけ**になる。
    """
    return render_template("admin_org.html", orgs=OrgProfile.all_companies())


@bp.route("/admin/org/<int:oid>", methods=["POST"])
@login_required
@role_required("owner")
def org_update(oid):
    """自社の情報。**法人ごとに保存する。**"""
    o = db.session.get(OrgProfile, oid) or abort(404)
    if "in_use" in request.form or request.form.get("_has_in_use"):
        new = bool(request.form.get("in_use"))
        if o.in_use != new:
            ChangeHistory.record("org", o.id, "利用",
                                 "使っている" if o.in_use else "使っていない",
                                 "使っている" if new else "使っていない",
                                 current_user.id)
            o.in_use = new
    for f in ("stale_days", "renew_notice_days"):
        if f not in request.form:
            continue
        raw = (request.form.get(f) or "").strip()
        new = int(raw) if raw.isdigit() and int(raw) > 0 else None
        if getattr(o, f) != new:
            ChangeHistory.record("org", o.id, f, getattr(o, f), new,
                                 current_user.id)
            setattr(o, f, new)
    for f in ("name", "postal_code", "address", "tel", "email",
              "invoice_reg_no", "bank", "seal_url", "note"):
        # **送られてこなかった項目は触らない。**
        # 触ると、フォームの一部だけを送っただけで他の値が消える（土台 §4.4）
        if f not in request.form:
            continue
        old = getattr(o, f)
        new = (request.form.get(f) or "").strip() or None
        if old == new:
            continue
        setattr(o, f, new)
        ChangeHistory.record("org", o.id, f, old, new, current_user.id)
    if request.form.get("is_display"):
        # **画面に出す法人は1つだけ。**複数立つと、どちらが出るか分からなくなる
        for x in OrgProfile.query.all():
            x.is_display = (x.id == o.id)
    db.session.commit()
    flash(f"{o.label} の設定を保存しました。")
    return redirect(url_for("admin.org"))


@bp.route("/admin/choices")
@login_required
@role_required("owner")
@feature_required("choices")
def choices():
    """選択肢のマスタ。**自由入力にすると表記がぶれるものだけ**をここで持つ。"""
    return render_template(
        "admin_choices.html", groups=CHOICE_GROUPS,
        rows={g: Choice.query.filter_by(group=g)
              .order_by(Choice.archived_at.isnot(None),
                        Choice.sort_order, Choice.id).all()
              for g in CHOICE_GROUPS})


@bp.route("/admin/choices/add", methods=["POST"])
@login_required
@role_required("owner")
@feature_required("choices")
def choice_add():
    group = request.form.get("group")
    label = (request.form.get("label") or "").strip()
    if group in CHOICE_GROUPS and label:
        if not Choice.query.filter_by(group=group, label=label).first():
            db.session.add(Choice(group=group, label=label))
            db.session.commit()
            flash(f"「{label}」を追加しました。")
        else:
            flash(f"「{label}」は既にあります。")
    return redirect(url_for("admin.choices"))


@bp.route("/admin/choices/<int:rid>/toggle", methods=["POST"])
@login_required
@role_required("owner")
@feature_required("choices")
def choice_toggle(rid):
    """**消さずに下げる。**過去のデータがこの値を指しているため。"""
    r = db.session.get(Choice, rid) or abort(404)
    r.archived_at = None if r.archived_at else utcnow()
    db.session.commit()
    flash(f"「{r.label}」を{'戻しました' if not r.archived_at else '選べないようにしました'}。")
    return redirect(url_for("admin.choices"))


@bp.route("/admin/features")
@login_required
@role_required("master")
def features():
    """機能の公開状態。**マスターだけ。**

    先方には公開したものだけを見せる。順に確認してもらうため、
    こちらの都合で出し入れできるようにしておく。
    """
    return render_template("admin_features.html", rows=Feature.all_rows(),
                           FEATURE_LABEL=FEATURE_LABEL)


@bp.route("/admin/features/<int:fid>", methods=["POST"])
@login_required
@role_required("master")
def feature_toggle(fid):
    f = db.session.get(Feature, fid) or abort(404)
    f.released = not f.released
    ChangeHistory.record("feature", f.id, FEATURE_LABEL.get(f.key, f.key),
                         "非公開" if f.released else "公開",
                         "公開" if f.released else "非公開", current_user.id)
    db.session.commit()
    flash(f"「{FEATURE_LABEL.get(f.key, f.key)}」を"
          f"{'公開しました' if f.released else '非公開にしました'}。")
    return redirect(url_for("admin.features"))
