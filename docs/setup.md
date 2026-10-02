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
v4l2-ctl -d /dev/video0 --list-formats-ext   # MJPG で 1280x720 があるか
```

試験で使っている ELP の USB カメラ（HD USB Camera）は、1280x720 だと 120fps しか選べない
（30 を頼んでも 120 で開く。実際に届くのは明るさしだいで毎秒 50〜60 枚）。
本体が `camera.fps`（30）を上限に間引くので、送るのは毎秒 30 枚前後になる。

## 4. 毎秒何枚撮れるかを測る

```bash
sudo systemctl stop fservice-pi
cd /opt/fservice-pi
sudo -u fservice /opt/fservice-pi/venv/bin/python -m fservice_pi bench --config /etc/fservice-pi/config.toml
# 解像度を変えて比べる
sudo -u fservice /opt/fservice-pi/venv/bin/python -m fservice_pi bench --config /etc/fservice-pi/config.toml --width 1920 --height 1080
```

見るもの: `[1] カメラ単体` と `[3] 通し` の枚数/秒（`送る分` が 30 前後）、`圧縮待ち満杯` の回数、CPU 温度。

実機（Pi 5 4GB・ELP 1280x720）の結果: カメラ 49〜61 枚/秒、1 枚の処理 約 2.4ms（縮小 0.1・動き判定 0.9・JPEG 1.3）、
JPEG 約 18KB、毎秒 61 枚通しても取りこぼし 0、CPU 50℃。

## 5. 動かす

```bash
sudo systemctl restart fservice-pi
journalctl -u fservice-pi -f      # 60 秒ごとに「申告」が出る
```

店に割り当てる前（トークンが無い間）は撮らない。割り当てても、管理画面で撮影のスイッチが「切」なら
カメラは止まったまま（申告の `cam_fps` が -1）。

## 5b. 外から操作できるようにする（Raspberry Pi Connect）

`ssh` は同じ Wi-Fi の中からしか入れない。外からも触れるように、事務所にいるうちに入れておく。

```bash
sudo apt install -y rpi-connect-lite
rpi-connect on
loginctl enable-linger        # ログアウトしても動き続ける
rpi-connect signin            # 出た URL をブラウザで開き、会社の Raspberry Pi ID で承認
```

以後は https://connect.raspberrypi.com → Devices → 端末の「Connect via」→「Remote shell」で、
ブラウザの中で同じ黒い画面が使える。

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
