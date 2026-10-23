#!/bin/bash
# 個室を 1 部屋足す。
#   sudo ./deploy/add-room.sh room1
# 部屋の名前は英小文字・数字・ハイフン（例: room1, vip-2）。カメラの fscam.txt の cam= と同じにする。
set -euo pipefail

ROOM="${1:-}"
if [ "$(id -u)" -ne 0 ]; then
  echo "root で実行してください（sudo）" >&2
  exit 1
fi
if ! [[ "$ROOM" =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]]; then
  echo "部屋の名前は英小文字・数字・ハイフンで（例: room1）: '$ROOM'" >&2
  exit 1
fi

SRC="$(cd "$(dirname "$0")" && pwd)"
CONF_DIR=/etc/fservice-pi/rooms
CONF="$CONF_DIR/$ROOM.toml"
DATA=/var/lib/fservice-pi/rooms/$ROOM
MAIN_CONF=/etc/fservice-pi/config.toml

BASE_URL=$(sed -n 's/^base_url *= *"\(.*\)".*/\1/p' "$MAIN_CONF" 2>/dev/null | head -1)
BASE_URL=${BASE_URL:-https://service.face-system.jp}
TID="pi-$(head -c 12 /etc/machine-id)-$ROOM"

mkdir -p "$CONF_DIR" "$DATA/inbox"
chown -R fservice:fservice /var/lib/fservice-pi/rooms
if [ -f "$CONF" ]; then
  echo "部屋 $ROOM はもうあります（$CONF）。設定はそのまま"
else
  sed -e "s|@BASE_URL@|$BASE_URL|" -e "s|@TID@|$TID|" -e "s|@ROOM@|$ROOM|g" \
    "$SRC/room.example.toml" > "$CONF"
  chgrp fservice "$CONF"
  chmod 0640 "$CONF"
  echo "部屋 $ROOM を足しました（$CONF）"
fi

systemctl restart fservice-rooms
echo
echo "管理画面の「未割当の端末」に $TID が出たら、店に割り当ててください。"
echo "カメラの fscam.txt には cam=$ROOM と、合言葉 key=$(cat /etc/fservice-pi/hub.key 2>/dev/null) を書きます。"
