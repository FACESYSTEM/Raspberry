"""ログイン・招待・パスワード再設定。

**招待はリンク1本で完結させる。**管理者が初期パスワードを決めて口頭で伝える
運用にすると、その平文がどこかに残る。
"""

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, session, url_for)
from flask_login import current_user, login_required, login_user, logout_user

from ..timeutil import utcnow
from ..extensions import db
from ..mailer import send
from ..models import ChangeHistory, User
from ..security import (PREVIEW_KEY, PREVIEW_SCOPE_KEY, PREVIEWABLE,
                        ROLE_LABEL, real_is_master)

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.is_active and user.check_password(
                request.form.get("password") or ""):
            login_user(user)
            user.last_login_at = utcnow()
            db.session.commit()
            return redirect(request.args.get("next") or url_for("main.dashboard"))
        # **どちらが違うかを教えない。**利用者の存在が分かってしまう
        flash("メールアドレスかパスワードが違います。")
    return render_template("login.html")


@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    """パスワードを忘れたとき。**再設定のリンクをメールで送る。**

    **相手が居るかどうかを画面に出さない。**「登録があれば送りました」と
    常に同じ返事にする。出すと、どのメールアドレスが登録されているかを
    外から調べられてしまう（ログインで「どちらが違うか教えない」のと同じ考え方）。

    メールが設定されていない環境では**届かない。**その場合は管理者が
    管理画面から再発行する必要があるので、画面にもそう書いておく。
    """
    # **メールが出ない環境で「送りました」と言わない。**
    # 届かないものを待たせるのが一番こたえる。先に断っておく
    can_mail = bool(current_app.config.get("SMTP_HOST"))
    if request.method == "POST" and can_mail:
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first()
        if user and user.is_active:
            invite_and_mail(user)
            ChangeHistory.record("user", user.id, "パスワード再設定",
                                 "", "リンクを発行", user.id)
            db.session.commit()
        # **居ても居なくても同じ返事。**
        return render_template("forgot_sent.html", email=email)
    return render_template("forgot.html", can_mail=can_mail)


@bp.route("/preview/<onoff>", methods=["GET", "POST"])
@login_required
def preview(onoff):
    """別の役割の見え方に切り替える。**マスターだけ。**

    アカウントを作らずに確認できるようにする。作って回ると、
    **確認用アカウントが本番に残る。**
    **抜ける経路は `real_is_master` で判定する。**確認モード中は
    マスター扱いにならないので、通常の判定だと抜けられなくなる。
    """
    if not real_is_master():
        abort(403)
    back = request.form.get("back") or request.referrer or url_for("main.dashboard")
    if onoff in PREVIEWABLE:
        session[PREVIEW_KEY] = onoff
        sid = (request.form.get("scope_id") or "").strip()
        session[PREVIEW_SCOPE_KEY] = (
            int(sid) if onoff == "outside" and sid.isdigit() else None)
        # **記録に残す。**あとから「誰が見たのか」を追えるようにする
        ChangeHistory.record("user", current_user.id, "見え方の確認",
                             "", ROLE_LABEL.get(onoff, onoff), current_user.id)
        db.session.commit()
    else:
        session.pop(PREVIEW_KEY, None)
        session.pop(PREVIEW_SCOPE_KEY, None)
    return redirect(back)


@bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))


@bp.route("/invite/<token>", methods=["GET", "POST"])
def invite(token):
    """招待リンクからパスワードを決める。**期限切れは通さない。**"""
    user = User.query.filter_by(invite_token=token).first()
    if user is None or not user.invite_alive:
        # **「使えません」だけで終わらせない。**
        # 一度使うと合言葉は消えるので、ここでは「使用済み」と「間違い」を
        # 見分けられない。**どちらの人にも次の一手が分かる**書き方にする。
        # 実際、設定を済ませた方が再びリンクを開いて行き止まりになった
        return render_template("invite_gone.html"), 410
    if request.method == "POST":
        pw = request.form.get("password") or ""
        if len(pw) < 12:
            flash("パスワードは12文字以上にしてください。")
        elif pw != (request.form.get("password2") or ""):
            flash("2つの欄が一致しません。")
        else:
            user.set_password(pw)          # ここで招待の合言葉も消える
            ChangeHistory.record("user", user.id, "パスワード",
                                 "", "設定しました", user.id)
            db.session.commit()
            login_user(user)
            return redirect(url_for("main.dashboard"))
    return render_template("invite.html", user=user)


def invite_and_mail(user: User) -> str:
    """招待を発行して知らせる。**リンクは呼び側にも返す**（メールが出ない環境用）。"""
    token = user.issue_invite(current_app.config["INVITE_TTL_HOURS"])
    db.session.commit()
    url = current_app.config["BASE_URL"] + url_for("auth.invite", token=token)
    send(user.email, "アカウントの設定のお願い",
         f"{user.name} 様\n\n下記から設定してください。\n{url}\n")
    return url
