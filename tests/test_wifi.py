import datetime as dt
import subprocess

import pytest

from fservice_pi import wifi


class FakeRun:
    def __init__(self, fail_add=False):
        self.calls = []
        self.fail_add = fail_add

    def __call__(self, args, **kw):
        self.calls.append(args)
        bad = self.fail_add and args[2] == "add"
        return subprocess.CompletedProcess(args, 1 if bad else 0, "", "error: bad" if bad else "")


def test_parse_blocks_comments_and_bom():
    text = "﻿# 説明\nssid=Port Ellen\npassword=abcd1234\n\nSSID = guest\r\nssid=open-net\n"
    nets = wifi.parse(text)
    assert [(n.ssid, n.password) for n in nets] == [("Port Ellen", "abcd1234"), ("guest", ""), ("open-net", "")]


@pytest.mark.parametrize("text", [
    "password=abc12345\n",
    "ssid=\n",
    "ssid=a\nfoo=bar\n",
    "ssid=a\nこれは説明\n",
    "{\\rtf1\\ansi ssid=a}",
])
def test_parse_errors(text):
    with pytest.raises(wifi.ParseError):
        wifi.parse(text)


def test_template_has_nothing_to_apply(tmp_path):
    assert wifi.parse(wifi.TEMPLATE) == []
    p = tmp_path / "fservice-wifi.txt"
    assert wifi.apply(p) == 0 and p.read_text() == wifi.TEMPLATE   # 無ければ見本を置く
    run = FakeRun()
    assert wifi.apply(p, run) == 0 and run.calls == []           # 見本のままなら何もしない


def test_apply_registers_and_erases_password(tmp_path):
    p = tmp_path / "fservice-wifi.txt"
    p.write_text("ssid=PortEllen\npassword=secretpass\n\nssid=short\npassword=123\n")
    run = FakeRun()
    assert wifi.apply(p, run, now=dt.datetime(2026, 10, 6, 14, 0)) == 1
    adds = [c for c in run.calls if c[2] == "add"]
    assert len(adds) == 1
    assert adds[0][adds[0].index("ssid") + 1] == "PortEllen"
    assert adds[0][adds[0].index("wifi-sec.psk") + 1] == "secretpass"
    out = p.read_text()
    assert "secretpass" not in out and "123\n" not in out
    assert "# OK  PortEllen" in out and "# NG  short（パスワードは 8〜63 文字）" in out
    assert wifi.parse(out) == []                                 # 次の起動では何もしない


def test_apply_reports_nmcli_failure_and_bad_file(tmp_path):
    p = tmp_path / "fservice-wifi.txt"
    p.write_text("ssid=x\npassword=abcdefgh\n")
    wifi.apply(p, FakeRun(fail_add=True))
    assert "# NG  x（error: bad）" in p.read_text()
    p.write_text("{\\rtf1 ssid=x}")
    assert wifi.apply(p, FakeRun()) == 1
    assert "リッチテキスト" in p.read_text()
