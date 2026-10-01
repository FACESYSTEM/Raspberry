"""サーバが配る撮影の時間帯（/v1/config の business_hours）を読む。

形: `12345 11:00-15:00;67 17:00-21:00`
- `;` で帯を区切る。帯は「曜日の数字 時刻範囲」。曜日は 1=月 … 7=日（specs.md A-153「日曜(7)」）
- 範囲は `,` か空白で複数並べてもよい
- 終わりが始まりより前なら 0 時またぎ（その曜日の夜に始まり、翌日の朝に終わる）
- サーバは営業時間を前後に広げた「撮影窓」（C-7）を、祝日を焼き込んで（A-153）配ってくる

読めないときは「常に撮る」に倒す（録り逃がさない。Android 版の設置チェックリスト #7 の
「未設定だと判定不能」で撮れなかった事故を Pi 版では起こさない）。
"""

from __future__ import annotations

import datetime as dt
import re

_RANGE = re.compile(r"^(\d{1,2}):(\d{2})-(\d{1,2}):(\d{2})$")


class Hours:
    def __init__(self, periods: list[tuple[set[int], int, int]] | None):
        # None ＝ 読めない／空 ＝ 常に撮る
        self.periods = periods

    @property
    def always(self) -> bool:
        return self.periods is None

    def open_at(self, when: dt.datetime) -> bool:
        if self.periods is None:
            return True
        dow = when.isoweekday()
        yesterday = 7 if dow == 1 else dow - 1
        minute = when.hour * 60 + when.minute
        for days, start, end in self.periods:
            if start < end:
                if dow in days and start <= minute < end:
                    return True
            else:  # 0 時またぎ（start == end は丸一日）
                if dow in days and minute >= start:
                    return True
                if yesterday in days and minute < end:
                    return True
        return False


def parse(text: str | None) -> Hours:
    """読めなければ Hours(None)＝常に撮る。"""
    if not text or not text.strip():
        return Hours(None)
    periods: list[tuple[set[int], int, int]] = []
    try:
        for part in text.split(";"):
            part = part.strip()
            if not part:
                continue
            tokens = part.replace(",", " ").split()
            days_tok, ranges = tokens[0], tokens[1:]
            if not days_tok.isdigit() or not ranges:
                return Hours(None)
            days = {int(c) for c in days_tok}
            if not days <= set(range(1, 8)):
                return Hours(None)
            for r in ranges:
                m = _RANGE.match(r)
                if not m:
                    return Hours(None)
                h1, m1, h2, m2 = (int(x) for x in m.groups())
                if h1 > 24 or h2 > 24 or m1 > 59 or m2 > 59:
                    return Hours(None)
                periods.append((days, h1 * 60 + m1, h2 * 60 + m2))
    except (ValueError, IndexError):
        return Hours(None)
    return Hours(periods) if periods else Hours(None)
