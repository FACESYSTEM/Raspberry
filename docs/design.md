# F-SERVICE 撮影端末 Raspberry Pi 版 — 設計

## 目的

**試験店で Pixel の隣に Pi を 1 台置き、サーバには別の端末として生コマを送る。**（一旦のゴール）

端末の仕事は「全コマを撮る → 動きのないコマを間引く → 残りをサーバへ送る」だけ。
顔の検出・追跡・照合・束ねはすべてサーバが行う。F-DOOR・ミラー・音声は対象外。

## 機材（購入済み）

| | |
|---|---|
| 本体 | Raspberry Pi 5 4GB（Active Cooler・PD 電源・32GB microSD つきキット） |
| カメラ | ELP USB カメラ 2MP（UVC・110° 広角・歪みなしレンズ・MJPEG） |

## データの流れ

```
USB カメラ（MJPEG 1280×720 30fps）
   ↓ 撮影スレッド
中央を正方形に切り出し → 640×640（Android 版に合わせる）
   ↓ 動きの判定（16×16 セル・T=8。R-164 と同じ値）→ 動きの時刻を記録
   ↓ 圧縮待ち（32 枚）── 満杯 ──→ ディスクへ逃がす（spill/）
   ↓ 圧縮スレッド（空いたらディスクの残りを拾う）
JPEG（q=60・1 枚 200KB 以下）
   ↓ 束ね: そのコマの 5 秒後まで判定が済んだら、保護帯の内→送る／外→間引く
ZIP（無圧縮・<t>r.jpg・60 枚か 10 秒・8MB 以下）→ 送信待ち（outbox/・ディスク）
   ↓ 送信スレッド（古い順。失敗したら送り直す）
POST /v1/detframes
```

並行して、心拍スレッドが `GET /v1/config`（30 秒ごと）を叩いて撮影窓・`face_enabled`・`face_params` を受け取り、
見張りが 60 秒ごとに申告（`kind:"stat"`）を `POST /v1/detlog` へ送る。

## Android 版との対応

| Android（FaceCaptureService.kt） | Pi 版 | 備考 |
|---|---|---|
| `frameSmallNv21()` 640×640 | `camera.square()` 640×640 | |
| 差分判定（Y 面の 256 セル平均） | `motion.MotionJudge`（グレーの 256 セル平均） | 値は同じ（T=8・保護帯 5 秒） |
| `raw_diff_filter` 0/1/2 | 同じ | 既定はサーバの `face_params` に従う。来なければ設定の値 |
| `FrameSpill`（R-14） | `spill.Spill` | .processing への改名・起動時の戻し・数え方も同じ |
| `detFramesFlush`（束・10 秒・60 枚） | `uploader.Batcher` | 束の時計は壁時計 |
| 送れなければ未送信で溜める | `uploader.Outbox`（ディスク） | 回線断でも撮影は止めない |
| 申告 `spill_*`・`raw_sent` など | 同じ名前で出す | |
| 撮影ウィンドウ（サーバ配信） | 同じ（心拍の `business_hours`） | サーバと繋がる前だけ設定の `capture.windows` |
| DPC・F-GUARD・FCM による起こし合い | **systemd の自動再起動＋ウォッチドッグ** | 背景実行の禁止・凍結・HOME 設定の問題は Linux には無い |
| 端末のログ（bugreport は人が押す） | `journalctl -u fservice-pi` | 全部取れる |

## 止まらないための仕組み

| 何が固まったら | 何が起こすか |
|---|---|
| アプリが落ちた | systemd が 5 秒で起こす（回数の上限なし） |
| 撮影スレッドが固まった（カメラの読み込みで止まった等） | 撮影スレッドが回っているときだけウォッチドッグを撫でる。60 秒止まれば systemd が落として起こす |
| カメラがコマを出さない | 10 秒で開き直す |
| OS ごと固まった | 基板のハードウェアウォッチドッグ（15 秒）が再起動する |
| 電源が落ちた | 電源が戻れば自動で起動し、送信待ち・退避の残りから送り直す |

## 申告（`/v1/detlog` の `kind:"stat"` の `det_frames`）

| 項目 | 意味 |
|---|---|
| `raw_frames` | カメラから届いたコマ（累計） |
| `raw_sent` | サーバへ送れたコマ |
| `diff_dropped` | 無人として間引いたコマ（`raw_diff_filter=2`） |
| `diff_would_drop` | 影モード（1）で、間引くはずだったコマ |
| `raw_dropped` | 圧縮待ちも退避も満杯で捨てたコマ（**0 であるべき**） |
| `spilled` / `spill_*` | ディスクへ逃がしたコマ・いま残っている枚数など |
| `outbox_*` | 送信待ちの束・枚数・大きさ |
| `outbox_full_dropped` / `too_big_dropped` / `encode_err` | 空き不足・1 枚 200KB 超・JPEG 化の失敗で捨てたコマ |
| `cam_fps` | 直近 10 秒の取得 fps（撮影窓の外は -1） |

**撮れたコマ ＝ 送れた ＋ 送信待ち ＋ 間引いた ＋ 退避中 ＋ 捨てた（理由別）** が常に成り立つ。
作り物のカメラと仮のサーバで確かめた（745 ＝ 360 ＋ 176 ＋ 209 ＋ 0）。

## サーバとのやりとり（Api.kt・f_voice_web.py の実物に合わせた）

| 口 | いつ | 中身 |
|---|---|---|
| `POST /v1/announce` | トークンが無い間、心拍の間隔で | `terminal_instance_id`・`device_model`・`os_version`・`app`（`jp.facesystem.fservice.pi`）・`version_code`。管理画面で店に割り当てると `assigned=true` とトークンが返る。トークンは `/var/lib/fservice-pi/token`（0600）に保存 |
| `GET /v1/config` | 30 秒ごと | クエリは Api.kt と同じ名前（`version`・`temp`・`uptime`・`app_uptime`・`fps`・`cfps`・`face_pending`・`face_saved`・`face_standby`・`drop`・`hbf`・`rssi`・`ip`・`memfree` など）。電池は無いので `battery=-1`・`charging=true`。返事の `business_hours`（撮影窓）・`face_enabled`・`face_params` に従う |
| `POST /v1/detframes` | 束ができたら | `application/zip`。**返事が JSON で `"ok": true` のときだけ送れたことにする** |
| `POST /v1/detlog` | 60 秒ごと | `application/x-ndjson`。`{"t":…,"kind":"stat","det_frames":{…}}` の 1 行 |

- 店はトークンからサーバが決める（`store_id` をクエリで送らない）
- 200 でも本文が JSON でなければ送れていない（店の Wi-Fi の同意ページ）→ 送り直す
- 401/403 はトークンの問題なので、生コマは捨てずに送り直す。それ以外の 4xx（400・413 など）は中身の拒否なので `failed/` へ退避（消さない）
- 撮影窓 `12345 11:00-15:00;67 17:00-21:00` は 1=月 … 7=日。終わりが始まりより前なら 0 時またぎ。**読めなければ常に撮る**（録り逃がさない）

## 確認が要ること（残り）

1. **受け取った `f_voice_web.py` は少し前の版**で、`/v1/detframes`・`/v1/detlog` が入っていない。
   この 2 つは Api.kt と specs.md に合わせた。いまのサーバで受け口の条件が変わっていないか
2. **管理画面での見え方**。`app=jp.facesystem.fservice.pi` で名乗るので、割り当ての画面で
   F-SERVICE と別のアプリとして出るはず。そのまま割り当てられるか
3. **監視の扱い**。Pi には FCM のトークンも DPC も無い。ops_watchdog の復旧段（FCM・DPC）が
   空振りして警報が鳴らないか。試験の間はミュート（`alert_mute.json`）を使うか

## 並設テストで見ること

- `cam_fps`（30 に届くか）と `raw_dropped`（0 か）
- 温度（Active Cooler で何度か）
- 同じ時刻のコマを Pixel と見比べる（画角・明るさ・ブレ）。ELP は 110° の広角なので、同じ距離でも顔は Pixel より小さく写る
- サーバ側の束ね・照合が Pi のコマでどうなるか（B-1 の物差し・同じ営業日で比べる）

## 決めたこと

- カメラの読み込みは OpenCV（V4L2）。Linux のカメラの標準の口と libjpeg-turbo を呼んでいるだけで、他に替えても速さは変わらない
- 解像度は Android 版と同じ 640×640。サーバとの比較が効くように、最初は合わせる
- fps は 30 を目標にする。出なければ出た分で動く（止まらない）。実際の値は `bench` と心拍の `cam_fps` で見る
- 設定は事務所で書いて出荷する（現地では触らない）

## 気をつけること

- **microSD（32GB）は書き込みに弱い。** 退避と送信待ちが SD に書かれる。回線が長く切れると数 GB 書く。
  試験はこのままでよいが、店に置くなら USB SSD か NVMe を足す
- 送信待ちは空きが `min_free_mb`（既定 4GB）を切ると書かない。32GB の SD なら OS を除いて 20GB 前後が上限
