"""メール送信。**設定が無ければ送らずログに出す。**

開発でSMTPを持たないことは普通にあるので、そこで落とさない。ただし
「送ったつもり」にならないよう、送っていないことをログに残す。
"""
import smtplib
from email.message import EmailMessage

from flask import current_app


def send(to: str, subject: str, body: str) -> bool:
    """送れたら True。**送れなくても例外にしない**（画面を止めない）。"""
    c = current_app.config
    if not (c.get("SMTP_HOST") and to):
        current_app.logger.info("[mail] 未設定のため送信しません to=%s 件名=%s",
                                to, subject)
        return False
    msg = EmailMessage()
    msg["From"] = c.get("MAIL_FROM") or c.get("SMTP_USER")
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(c["SMTP_HOST"], c["SMTP_PORT"], timeout=20) as s:
            s.starttls()
            if c.get("SMTP_USER"):
                s.login(c["SMTP_USER"], c["SMTP_PASSWORD"])
            s.send_message(msg)
        return True
    except Exception as e:                        # noqa: BLE001
        # **本文は出さない。**業務データがログに残る
        current_app.logger.warning("[mail] 送信できませんでした to=%s: %s", to, e)
        return False
