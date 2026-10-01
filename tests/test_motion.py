import numpy as np

from fservice_pi.motion import GUARD_MS, MotionJudge, MotionLog, cell_means


def test_cell_means_is_block_average():
    g = np.zeros((640, 640), np.uint8)
    g[:40, :40] = 200  # 左上の 1 セルだけ
    cells = cell_means(g)
    assert cells.shape == (16, 16)
    assert cells[0, 0] == 200
    assert cells[0, 1] == 0 and cells[1, 0] == 0


def test_judge_first_frame_is_motion_then_threshold():
    j = MotionJudge()
    g = np.full((640, 640), 100, np.uint8)
    assert j.judge(g) is True  # 先頭は判定できない＝動き
    assert j.judge(g.copy()) is False
    h = g.copy()
    h[:40, :40] = 107  # セル差 7 → 動きではない
    assert j.judge(h) is False
    k = h.copy()
    k[:40, :40] = 115  # セル差 8 → 動き
    assert j.judge(k) is True


def test_judge_bad_input_is_motion():
    j = MotionJudge()
    j.judge(np.zeros((640, 640), np.uint8))
    assert j.judge(None) is True


def test_guard_band_keep_drop_pending():
    m = MotionLog()
    # 0..30 秒、100ms ごと。動きは 0 秒（先頭）と 12.0 秒
    for t in range(0, 30_001, 100):
        m.record(t, t == 12_000 or t == 0)
    assert m.decide(3_000) == MotionLog.KEEP  # 0 秒の保護帯
    assert m.decide(5_000) == MotionLog.KEEP
    assert m.decide(5_100) == MotionLog.DROP
    assert m.decide(6_900) == MotionLog.DROP
    assert m.decide(12_000 - GUARD_MS) == MotionLog.KEEP
    assert m.decide(12_000 + GUARD_MS) == MotionLog.KEEP
    assert m.decide(12_000 + GUARD_MS + 100) == MotionLog.DROP
    # 30 秒が最新なので、25 秒より後はまだ決まらない
    assert m.decide(26_000) == MotionLog.PENDING
    assert m.decide(26_000, force=True) == MotionLog.KEEP


def test_older_than_history_is_keep():
    m = MotionLog(retention_ms=60_000)
    for t in range(0, 200_001, 1000):
        m.record(t, False)
    # 履歴（60 秒）より古いものは判定不能＝送る
    assert m.decide(10_000) == MotionLog.KEEP
    assert m.decide(180_000) == MotionLog.DROP


def test_clock_going_back_resets():
    m = MotionLog()
    for t in range(100_000, 120_001, 100):
        m.record(t, False)
    m.record(50_000, False)  # 時計が戻った
    assert m.decide(60_000) == MotionLog.PENDING
    assert m.decide(40_000) == MotionLog.KEEP  # 戻った後の起点より前＝判定不能＝送る
