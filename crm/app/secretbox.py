"""あとで人が読む必要がある秘密の暗号化。

パスワードのように**照合できればよいもの**はハッシュで持つ（`models.User`）。
ここに入れるのは**取り出して使うもの**（外部サービスのログイン情報、
APIのトークンなど）。ハッシュにすると取り出せない。

鍵は `.env` の `CREDENTIAL_KEY`。**DBには入れない。**DBのバックアップが
漏れても、鍵が別なら読めない。

**鍵が無ければ保存しない。**「暗号化できないので平文で置く」を起こさない。

    python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"

**鍵を変えると、それまでに保存したものは読めなくなる。**控えを別に持つこと。
"""
from __future__ import annotations

from flask import current_app

ENV_KEY = "CREDENTIAL_KEY"


def _fernet():
    key = current_app.config.get(ENV_KEY) or ""
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:                      # 鍵の形が違う（打ち間違いなど）
        current_app.logger.warning("%s の形が正しくありません", ENV_KEY)
        return None


def ready() -> bool:
    """保存できる状態か。**画面に出して、入れられない理由を隠さない。**"""
    return _fernet() is not None


def seal(plain: str) -> str:
    """しまう。**鍵が無ければ例外。**黙って平文で置かない。"""
    f = _fernet()
    if f is None:
        raise RuntimeError(f"{ENV_KEY} が設定されていません")
    return f.encrypt((plain or "").encode("utf-8")).decode("ascii")


def open_(sealed: str) -> str:
    """取り出す。読めなければ空文字（鍵を変えた・壊れた）。"""
    f = _fernet()
    if f is None or not sealed:
        return ""
    try:
        return f.decrypt(sealed.encode("ascii")).decode("utf-8")
    except Exception:
        current_app.logger.warning("保管した値を復号できませんでした")
        return ""


def mask(plain: str) -> str:
    """画面に出す伏せ字。**長さも隠す。**"""
    return "••••••••" if plain else ""
