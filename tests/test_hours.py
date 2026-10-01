import datetime as dt

from fservice_pi.hours import parse

# 2026-10-05 は月曜（isoweekday=1）
MON = dt.datetime(2026, 10, 5)


def at(day_offset, h, m=0):
    return MON + dt.timedelta(days=day_offset, hours=h, minutes=m)


def test_weekday_lunch_and_weekend_dinner():
    h = parse("12345 11:00-15:00;67 17:00-21:00")
    assert h.open_at(at(0, 11))           # 月 11:00
    assert not h.open_at(at(0, 15))       # 月 15:00 は終わり
    assert not h.open_at(at(0, 18))       # 月の夜は無い
    assert h.open_at(at(5, 18))           # 土 18:00
    assert not h.open_at(at(5, 12))       # 土の昼は無い
    assert h.open_at(at(6, 20, 59))       # 日 20:59


def test_overnight_belongs_to_start_day():
    h = parse("5 17:00-02:00")            # 金の夜だけ
    assert h.open_at(at(4, 23))           # 金 23:00
    assert h.open_at(at(5, 1, 30))        # 土 01:30（金の続き）
    assert not h.open_at(at(5, 2))        # 土 02:00 終わり
    assert not h.open_at(at(3, 1))        # 木の深夜は無い（水の帯が無いので）
    assert not h.open_at(at(5, 23))       # 土の夜は無い


def test_sunday_is_7_and_wraps_to_monday():
    h = parse("7 22:00-03:00")
    assert h.open_at(at(6, 23))           # 日 23:00
    assert h.open_at(at(7, 2))            # 翌月曜 02:00


def test_24h_and_multiple_ranges():
    h = parse("1234567 00:00-23:59")
    assert h.open_at(at(2, 12))
    h = parse("12345 11:00-14:00,17:00-22:00")
    assert h.open_at(at(0, 12)) and h.open_at(at(0, 18)) and not h.open_at(at(0, 15))


def test_unreadable_means_always_capture():
    for text in ("", None, "毎日 10:00-20:00", "12345", "89 10:00-11:00", "1 25:00-26:00"):
        h = parse(text)
        assert h.always
        assert h.open_at(at(0, 3))


def test_server_to_compact_example():
    # business_hours.to_compact() の docstring の例そのもの
    h = parse("12345 11:00-15:00;12345 17:00-22:00")
    assert h.open_at(at(0, 12)) and h.open_at(at(4, 21, 59))
    assert not h.open_at(at(0, 16)) and not h.open_at(at(5, 12))
