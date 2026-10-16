"""設定。**既定値は安全側に倒す。**

環境変数から読む。`.env` は環境ごとに作り、他の環境の値を写さない。
"""
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# **`.env` をここで読む。**読まないと、`.env` に鍵を書いても効かない。
# 効かないまま既定値で動くので、書いた側は設定できたと思い込む。
# 既に環境変数がある場合はそちらを優先する（systemd の EnvironmentFile 等）
load_dotenv(BASE_DIR / ".env")

# **この値のままでは起動させない。**設定し忘れに気づけなくするくらいなら止める
INSECURE_SECRET_KEY = "dev-insecure-key"


class Config:
    # **既定値を本番で使える値にしない。**設定し忘れに気づけなくなる。
    # この値のまま起動しようとすると `create_app` が止める
    SECRET_KEY = os.environ.get("SECRET_KEY", INSECURE_SECRET_KEY)

    _db = os.environ.get("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'data' / 'app.db'}"
    SQLALCHEMY_DATABASE_URI = _db
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # HTTPSで運用するときは 1。既定を1にすると開発でログインできず原因が分かりにくい
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "0") == "1"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    ORG_NAME = os.environ.get("ORG_NAME", "組織名を設定してください")

    # 保管する秘密の暗号鍵。**空なら保存を拒む**（平文で置かないため）
    CREDENTIAL_KEY = os.environ.get("CREDENTIAL_KEY", "")

    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    MAIL_FROM = os.environ.get("MAIL_FROM", "")
    ALERT_TO = os.environ.get("ALERT_TO", "")

    # 招待・再設定リンクの有効時間
    INVITE_TTL_HOURS = int(os.environ.get("INVITE_TTL_HOURS", "72"))

# **切り出した部品を読めるようにする。**
# `packages/` に置いたものは、そのまま他のシステムへ持っていける
_PKG = BASE_DIR / "packages"
if str(_PKG) not in sys.path:
    sys.path.insert(0, str(_PKG))
