# F-SERVICE 撮影端末（Raspberry Pi 版）

店舗に置く撮影端末を Raspberry Pi で作る。Android 版（F-SERVICE）と同じサーバへ、同じ口で生コマを送る。

端末の仕事は **全コマを撮る → 動きのないコマを間引く → 残りをサーバへ送る** だけ。
顔の検出・照合・束ねはすべてサーバが行う。

- 設計: [docs/design.md](docs/design.md)
- 事務所での設定手順: [docs/setup.md](docs/setup.md)
- 設定の見本: [config.example.toml](config.example.toml)

## 使い方

```bash
python -m fservice_pi run   --config /etc/fservice-pi/config.toml   # 本番（systemd から）
python -m fservice_pi bench --config /etc/fservice-pi/config.toml   # 毎秒何枚撮れるかを測る
```

## 開発

```bash
pip install -r requirements.txt pytest
python -m pytest -q
```

カメラが無い環境では、設定の `camera.kind = "fake"` で作り物の絵を流せる。
