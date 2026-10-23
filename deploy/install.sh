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
install -m 0644 "$APP/systemd/fservice-wifi.service" /etc/systemd/system/fservice-wifi.service
systemctl daemon-reload

echo "== 個室の小型カメラ（受け口と、部屋ごとの端末）"
install -m 0644 "$APP/systemd/fservice-hub.service" /etc/systemd/system/fservice-hub.service
install -m 0644 "$APP/systemd/fservice-rooms.service" /etc/systemd/system/fservice-rooms.service
mkdir -p /etc/fservice-pi/rooms /var/lib/fservice-pi/rooms
chown fservice:fservice /var/lib/fservice-pi/rooms
# カメラとの合言葉（店内の別の機器から写真を送り込まれないように）。無ければ作る
if [ ! -s /etc/fservice-pi/hub.key ]; then
  head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n' > /etc/fservice-pi/hub.key
fi
chgrp fservice /etc/fservice-pi/hub.key
chmod 0640 /etc/fservice-pi/hub.key
systemctl daemon-reload
systemctl enable fservice-hub fservice-rooms
systemctl restart fservice-hub fservice-rooms

echo "== Wi-Fi を Mac から書き換える口（bootfs の fservice-wifi.txt）"
systemctl enable fservice-wifi
# 見本を置く（すでにあれば触らない）
"$APP/venv/bin/python" -c "import sys; sys.path.insert(0, '$APP'); from fservice_pi import wifi; wifi.apply()" || true

if [ ! -f "$CONF" ]; then
  mkdir -p "$(dirname "$CONF")"
  install -m 0640 -g fservice "$APP/config.example.toml" "$CONF"
  echo
  echo "設定の見本を $CONF に置きました（token は空のままでよい。管理画面で割り当てると自動で受け取る）。"
  echo "先にカメラの確認と bench（docs/setup.md）をしてから:"
  echo "  sudo systemctl enable --now fservice-pi"
  exit 0
fi
chgrp fservice "$CONF"
chmod 0640 "$CONF"
systemctl enable fservice-pi
systemctl restart fservice-pi
systemctl --no-pager status fservice-pi | head -5
