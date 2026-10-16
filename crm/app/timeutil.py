"""時刻の扱い。**保存はUTC、表示はJST。**

`datetime.utcnow()` は Python 3.12 で非推奨になり、将来のバージョンで削除される。
`datetime.now(timezone.utc)` に置き換えるが、**DBに入れる値はこれまでどおり
タイムゾーン無し（naive）のUTC**にする。既存データと同じ形なので移行が要らない。

**画面にはJSTで出す。**UTCのまま出すと9時間ずれる。変更履歴が9時間ずれると、
「いつ誰が変えたか」を追うという履歴の目的そのものが壊れる（規約 §6）。

日本にはサマータイムが無いので、JSTは固定の +09:00 でよい。
"""
from datetime import datetime, timedelta, timezone

JST = timezone(timedelta(hours=9), "JST")

# 画面に出す既定の書式。**秒は出さない**（読む側に意味が無い）
FMT = "%Y-%m-%d %H:%M"


def utcnow() -> datetime:
    """いまのUTC。**タイムゾーン無し**で返す（DBの列に合わせる）。

    `datetime.utcnow()` の置き換え。呼び出し側の書き方は変わらない。
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_jst(dt):
    """タイムゾーン無しのUTC → JST。**Noneはそのまま返す**（未設定は正常な状態）。

    すでにタイムゾーンが付いている値も受ける（外から来た値が混ざっても落ちない）。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(JST)


def fmt(dt, form: str = FMT, empty: str = "—") -> str:
    """画面用の文字列。**未設定は「—」**。

    テンプレートからは `{{ 値|jst }}` で呼ぶ（`app/__init__.py` で登録）。
    """
    got = to_jst(dt)
    return empty if got is None else got.strftime(form)
