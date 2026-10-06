"""Mac からでも書き換えられる Wi-Fi 設定（/boot/firmware/fservice-wifi.txt）。

Pi の起動ディスク（SD カードでも SSD でも）の最初の領域 bootfs は、Mac でもそのまま開ける形式（FAT）。
そこに置いた fservice-wifi.txt を、起動のたびに読んで NetworkManager に登録する。

  1. Pi の電源を切り、SSD（または SD カード）を Mac に挿す
  2. Mac に出てくる bootfs の fservice-wifi.txt に ssid= と password= を書いて保存
  3. Pi に戻して電源を入れる → 登録され、ファイルのパスワードは消して「反映済み」と書き直す

パスワードを書いたまま残さない（ディスクを抜けば誰でも読める場所なので）。
書くものが無ければファイルには触らない（起動のたびに書き込まない）。
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PATH = "/boot/firmware/fservice-wifi.txt"
CON_PREFIX = "fservice-"
PRIORITY = 10  # 事務所で Imager に入れた Wi-Fi（0）より優先して選ぶ

TEMPLATE = """\
# F-SERVICE Pi の Wi-Fi 設定
#
# 書き方: 下の例のように ssid=（Wi-Fi の名前）と password=（パスワード）を 2 行で書く。
#         Wi-Fi を何個か入れるときは、空行をはさんで続けて書く。
#         「#」で始まる行は読まない（説明用）。
#
# Pi が次に起動したときに登録して、ここに書いたパスワードは消し、結果を一番下に書き直す。
# 今までに登録した Wi-Fi はそのまま残る（消えない）。同じ名前を書けばパスワードを上書きする。
#
# Mac で書くとき: 「テキストエディット」なら、メニューの「フォーマット」→「標準テキストにする」
#                 にしてから保存する（リッチテキストのままだと読めない）。
#
# 例:
# ssid=PortEllen-WiFi
# password=abcd1234

"""


@dataclass
class Network:
    ssid: str
    password: str


class ParseError(ValueError):
    pass


def parse(text: str) -> list[Network]:
    """ssid= / password= の組を読む。読めない書き方は ParseError（何を直せばよいかを書く）。"""
    if text.lstrip("﻿").lstrip().startswith("{\\rtf"):
        raise ParseError("リッチテキストで保存されていて読めない。「標準テキストにする」にしてから保存し直してください")
    nets: list[Network] = []
    cur: dict[str, str] = {}

    def close():
        if not cur:
            return
        if "ssid" not in cur:
            raise ParseError("password= の前に ssid= がない")
        nets.append(Network(cur["ssid"], cur.get("password", "")))
        cur.clear()

    for n, raw in enumerate(text.lstrip("﻿").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ParseError(f"{n} 行目に = がない: {line[:30]}")
        key, value = line.split("=", 1)
        key = key.strip().lower()
        # 値の前後の空白は入力の揺れとして落とす（Wi-Fi 名やパスワードの端に空白がある例はまず無い）
        value = value.strip()
        if key == "ssid":
            close()
            if not value:
                raise ParseError(f"{n} 行目の ssid= が空")
            cur["ssid"] = value
        elif key in ("password", "pass", "psk"):
            if "password" in cur:
                raise ParseError(f"{n} 行目: 1 つの ssid に password= が 2 つある")
            cur["password"] = value
        else:
            raise ParseError(f"{n} 行目の「{key}」は知らない項目（ssid= と password= だけ書けます）")
    close()
    return nets


def check(net: Network) -> str:
    """登録できない値なら理由。よければ空。"""
    if len(net.ssid.encode()) > 32:
        return "Wi-Fi の名前が長すぎる（32 バイトまで）"
    if net.password and not 8 <= len(net.password) <= 63:
        return "パスワードは 8〜63 文字"
    return ""


def _nmcli(args: list[str], run=subprocess.run) -> tuple[bool, str]:
    try:
        p = run(["nmcli", *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    return p.returncode == 0, (p.stderr or p.stdout or "").strip()


def register(net: Network, run=subprocess.run) -> str:
    """NetworkManager に登録する。同じ名前のものは入れ替える。失敗なら理由。"""
    name = CON_PREFIX + net.ssid
    _nmcli(["connection", "delete", "id", name], run)  # 無ければ失敗するが構わない
    args = ["connection", "add", "type", "wifi", "con-name", name, "ifname", "wlan0",
            "ssid", net.ssid,
            "connection.autoconnect", "yes",
            "connection.autoconnect-priority", str(PRIORITY)]
    if net.password:
        args += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", net.password]
    ok, msg = _nmcli(args, run)
    return "" if ok else (msg or "nmcli が失敗")


def _write(path: Path, text: str):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


def apply(path: str | Path = DEFAULT_PATH, run=subprocess.run, now: dt.datetime | None = None) -> int:
    """ファイルを読んで登録する。書き直したら 1、何もしなければ 0。"""
    path = Path(path)
    if not path.exists():
        if path.parent.is_dir():
            _write(path, TEMPLATE)
            log.info("Wi-Fi 設定の見本を置いた: %s", path)
        return 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        log.error("Wi-Fi 設定を読めない %s: %s", path, e)
        return 0
    results: list[str] = []
    try:
        nets = parse(text)
    except ParseError as e:
        log.error("Wi-Fi 設定を読めない: %s", e)
        nets, results = [], [f"NG  読めませんでした: {e}"]
    if not nets and not results:
        return 0  # 書くものが無い。毎回の起動で書き込まない
    for net in nets:
        why = check(net) or register(net, run)
        if why:
            log.error("Wi-Fi を登録できない %s: %s", net.ssid, why)
            results.append(f"NG  {net.ssid}（{why}）")
        else:
            log.info("Wi-Fi を登録した: %s", net.ssid)
            results.append(f"OK  {net.ssid}")
    stamp = (now or dt.datetime.now()).strftime("%Y-%m-%d %H:%M")
    body = TEMPLATE + f"# --- 前回の反映 {stamp}（パスワードは消してあります）---\n"
    body += "".join(f"# {r}\n" for r in results)
    _write(path, body)
    return 1


def main(path: str = DEFAULT_PATH) -> int:
    apply(path)
    return 0  # 失敗しても起動を止めない（結果はファイルとログに残る）
