"""アプリを組み立てる。"""
import logging
import os
from pathlib import Path

from flask import Flask, render_template, request

from .config import Config, INSECURE_SECRET_KEY
from .extensions import csrf, db, login_manager


def _static_version(app) -> str:
    """静的ファイルの版。**変えたのに古いものが読まれる、を防ぐ。**

    CSSを直したのに画面が変わらないと、直っていないと判断されて
    別の直し方を試すことになる。**更新時刻から版を作り、URLに付ける。**
    """
    latest = 0
    static = Path(app.root_path) / "static"
    if static.exists():
        for f in static.rglob("*"):
            if f.is_file():
                latest = max(latest, int(f.stat().st_mtime))
    return str(latest)


def create_app(config_object=Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)

    # **鍵が既定値のままなら起動しない。**
    # `secretbox` が「鍵が無ければ保存を拒む」のと同じ考え方をここにも当てる。
    # 起動できてしまうと、`.env` を入れ忘れたことに**誰も気づけない**。
    # セッションが世に出回っている鍵で署名されたまま、何事もなく動き続ける
    if not app.config.get("TESTING") and app.config["SECRET_KEY"] == INSECURE_SECRET_KEY:
        raise RuntimeError(
            "SECRET_KEY が既定値のままです。.env に設定してください。\n"
            '  生成: python -c "import secrets;print(secrets.token_urlsafe(48))"'
        )

    Path(app.root_path).parent.joinpath("data").mkdir(exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)

    from .auth import bp as auth_bp
    from .admin import bp as admin_bp
    from .main import bp as main_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(admin_bp)

    # **修正モードは切り出してある**（`packages/fixmode`）。
    # 他のシステムでも同じものを使う。二重に持つと、必ず片方が古くなる。
    # 誰が何をできるかだけ、ここで渡す。
    from flask_login import current_user, login_required
    from fixmode import FixMode
    from .security import can_edit, is_master, is_owner
    FixMode(db).init_app(
        app,
        can_comment=can_edit,          # 自社の人は書ける
        can_fix=is_master,             # 直すのは当社だけ
        can_close=is_owner,            # 確認して閉じるのは先方（管理者以上）
        who=lambda: (getattr(current_user, "id", None),
                     getattr(current_user, "name", "")),
        login_required=login_required,
        base_template="base.html")

    # **外部から叩くAPIはCSRFの対象外にする。**送ってくるのはブラウザでは
    # ないのでトークンを持てない。保護はAPIキーで行う（`security` 参照）。
    # ここを外し忘れると、外部連携が黙って止まる
    # from .api import bp as api_bp
    # csrf.exempt(api_bp)

    # **画面の日時はJSTで出す。**UTCのまま出すと9時間ずれる（`timeutil` 参照）
    from .timeutil import fmt as _fmt
    app.jinja_env.filters["jst"] = _fmt

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("error.html", code=403,
                               message="この画面を見る権限がありません。"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("error.html", code=404,
                               message="見つかりませんでした。"), 404

    @app.context_processor
    def inject():
        """画面に出す名前は**DBから取る。**

        `.env` と二重に持つと、管理画面で変えても画面が変わらない。
        「設定したのに効かない」は、設定した側から見て原因が分からない。
        DBが読めないとき（初期化前など）だけ `.env` の値に落とす。
        """
        from .security import (ROLE_LABEL, can_edit, effective_role,
                               feature_open, is_master, is_owner,
                               preview_role, previewing, real_is_master)
        name = app.config["ORG_NAME"]
        try:
            from .models import OrgProfile
            got = OrgProfile.display()
            if got and got.name:
                name = got.name
        except Exception:                       # noqa: BLE001（表を作る前でも画面を出す）
            pass
        # 未記入の件数。**開かないページの中で警告しても気づけない**ので、
        # サイドバーの見出しに出す。管理者以外は開けないので数えない
        issues = 0
        try:
            if is_owner():
                from .models import OrgProfile                # noqa: PLC0415
                issues = sum(len(o.issues) for o in OrgProfile.all_companies()
                             if o.in_use)
        except Exception:                       # noqa: BLE001（表を作る前でも画面を出す）
            pass
        return {"ORG_NAME": name, "ORG_ISSUES": issues, "is_owner": is_owner, "is_master": is_master,
                "feature_open": feature_open, "previewing": previewing,
                "real_is_master": real_is_master, "preview_role": preview_role,
                "effective_role": effective_role,
                "can_edit": can_edit, "ROLE_LABEL": ROLE_LABEL,
                "STATIC_V": _static_version(app)}

    if not app.debug:
        logging.basicConfig(level=logging.INFO)
    return app
