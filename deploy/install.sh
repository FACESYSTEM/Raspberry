#!/bin/bash
# 事務所で 1 台ずつ流す設置スクリプト（Raspberry Pi OS Lite 64bit / Bookworm 想定）。
#   sudo ./deploy/install.sh
# 設定（/etc/fservice-pi/config.toml）は別に置く。無ければ見本を置いて止まる。
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/fservice-pi
CONF=/etc/fservice-pi/config.toml

if [ "$(id -u)" -ne 0 ]; then
  echo "root で実行してください（sudo）" >&2
  exit 1
fi

echo "== パッケージ"
apt-get update
apt-get install -y python3-venv python3-opencv python3-numpy python3-requests v4l-utils chrony

echo "== 時刻（日本時間・NTP）"
timedatectl set-timezone Asia/Tokyo
systemctl enable --now chrony

echo "== 利用者"
id fservice >/dev/null 2>&1 || useradd --system --home-dir "$APP" --shell /usr/sbin/nologin fservice
usermod -aG video fservice

echo "== 本体"
mkdir -p "$APP"
rsync -a --delete --exclude venv --exclude .git --exclude tests "$SRC/" "$APP/"
# OpenCV は apt のもの（Pi 向けに作られている）を使う
[ -d "$APP/venv" ] || python3 -m venv --system-site-packages "$APP/venv"
chown -R fservice:fservice "$APP"

echo "== ハードウェアのウォッチドッグ（OS ごと固まったら基板が再起動する）"
mkdir -p /etc/systemd/system.conf.d
cat > /etc/systemd/system.conf.d/watchdog.conf <<'EOF'
[Manager]
RuntimeWatchdogSec=15
RebootWatchdogSec=2min
EOF

echo "== 常駐"
install -m 0644 "$APP/systemd/fservice-pi.service" /etc/systemd/system/fservice-pi.service
systemctl daemon-reload

if [ ! -f "$CONF" ]; then
  mkdir -p "$(dirname "$CONF")"
  install -m 0640 -g fservice "$APP/config.example.toml" "$CONF"
  echo
  echo "設定の見本を $CONF に置きました。token と store_id を書いてから:"
  echo "  sudo systemctl enable --now fservice-pi"
  exit 0
fi
chgrp fservice "$CONF"
chmod 0640 "$CONF"
systemctl enable fservice-pi
systemctl restart fservice-pi
systemctl --no-pager status fservice-pi | head -5
