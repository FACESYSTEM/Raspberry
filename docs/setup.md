# 事務所での設定手順（出荷前）

**このリストを全部通した端末だけを店に出す。**

## 1. OS を書く

Raspberry Pi Imager で **Raspberry Pi OS Lite (64-bit)** を microSD に書く。書く前の設定（歯車）で:

- ホスト名: `fs-pi-<店>-<番号>`（例 `fs-pi-tsujido-1`）
- ユーザーとパスワード
- **店の Wi-Fi の SSID とパスワード**（事務所で先に入れておく）
- 地域: 日本・タイムゾーン Asia/Tokyo
- SSH を有効にする（公開鍵）

## 2. 本体を入れる

```bash
sudo apt install -y git rsync
git clone -b claude/raspberry-pi-face-auth-auf1cr https://github.com/FACESYSTEM/Raspberry.git
cd Raspberry
sudo ./deploy/install.sh
```

初回は設定の見本が `/etc/fservice-pi/config.toml` に置かれて止まる。
`server.base_url` を確かめて（トークンは空のままでよい）、`sudo systemctl enable --now fservice-pi`。

## 2b. 店に割り当てる

アプリが起動すると `/v1/announce` でサーバに名乗る（Android 版のコード入力なしのペアリングと同じ）。

1. `journalctl -u fservice-pi | grep 名乗り` で端末の名前（`pi-xxxxxxxxxxxx`）を確かめる
2. 管理画面で、その名前の端末を店に割り当てる
3. 30 秒以内に `店に割り当てられた: <店名>` と出る。トークンは `/var/lib/fservice-pi/token` に保存され、
   以後は再起動しても名乗り直さない

**並設テストでは、Pixel とは別の端末として割り当てる。**

## 3. カメラを確かめる

```bash
v4l2-ctl --list-devices
v4l2-ctl -d /dev/video0 --list-formats-ext   # MJPG で 1280x720 30fps があるか
```

## 4. 毎秒何枚撮れるかを測る

```bash
sudo systemctl stop fservice-pi
sudo -u fservice /opt/fservice-pi/venv/bin/python -m fservice_pi bench --config /etc/fservice-pi/config.toml
# 解像度を変えて比べる
sudo -u fservice /opt/fservice-pi/venv/bin/python -m fservice_pi bench --config /etc/fservice-pi/config.toml --width 1920 --height 1080
```

見るもの: `[1] カメラ単体` と `[3] 通し` の枚数/秒、`圧縮待ち満杯` の回数、CPU 温度。

## 5. 動かす

```bash
sudo systemctl restart fservice-pi
journalctl -u fservice-pi -f      # 60 秒ごとに「申告」が出る
```

## 6. 出荷前のテスト

| # | 項目 | 確認方法 |
|---|---|---|
| 1 | 心拍がサーバに来る | 管理画面で端末が「稼働」・`cam_fps` が 0 より大きい |
| 2 | 生コマが届く | 管理画面の生コマで、この端末の絵が見える |
| 3 | **再起動テスト**: 電源を抜いて挿し直し、5 分以内に 1 に戻る | 管理画面 |
| 4 | **放置テスト**: 触らず 15 分置いて止まらない | 管理画面 |
| 5 | **カメラ抜き差し**: USB を抜いて挿し直し、撮影が戻る | `journalctl` に「カメラを開き直す」→ `cam_fps` が戻る |
| 6 | **回線断**: Wi-Fi を切って 5 分後に戻し、溜まった分が送られる | 申告の `outbox_frames` が増えてから 0 に戻る |
| 7 | 捨てたコマが 0 | 申告の `raw_dropped`・`outbox_full_dropped` が 0 |

## 困ったとき

| 症状 | 見るところ |
|---|---|
| 動いているか | `systemctl status fservice-pi` |
| 何が起きたか | `journalctl -u fservice-pi --since "1 hour ago"` |
| 送れていない | 申告の `outbox_frames`・`send_retry`、`/var/lib/fservice-pi/failed/*.reason.txt` |
| カメラ | `v4l2-ctl --list-devices`・`dmesg | grep -i usb` |
