# 設置の手順（開発環境）

対象：`crm.〈example.com〉`（〈サーバーのIP〉／エックスサーバーVPS・Ubuntu 26.04 LTS）
`〈ホスト名〉` も同じアプリを指す（**古いリンクを切らさないため残してある**）。

**参照元アプリとは別ユーザー・別ディレクトリ・別DB・別ユニットにする**（設計指示書 §0.2）。

## 0. 前提

```
Linuxユーザー   〈appuser〉（sudo なし）
置き場所        /home/〈appuser〉/app
待ち受け        127.0.0.1:8010（外に直接は出さない）
前段            既存の Caddy（80/443）
DB              /home/〈appuser〉/app/data/app.db
```

## 1. ユーザーを作る（管理者権限で1回だけ）

```bash
sudo adduser --disabled-password --gecos "" 〈appuser〉
sudo install -d -m 700 -o 〈appuser〉 -g 〈appuser〉 /home/〈appuser〉/.ssh
echo '<公開鍵>' | sudo tee /home/〈appuser〉/.ssh/authorized_keys
sudo chmod 600 /home/〈appuser〉/.ssh/authorized_keys
sudo chown 〈appuser〉:〈appuser〉 /home/〈appuser〉/.ssh/authorized_keys
```

## 2. コードを置く（〈appuser〉 で）

```bash
ssh -i ~/.ssh/〈app〉_crm 〈appuser〉@〈サーバーのIP〉
mkdir -p ~/app && cd ~/app
# 手元から: rsync -az --exclude .venv --exclude data --exclude backups \
#            --exclude download --exclude .env ./ 〈appuser〉@〈サーバーのIP〉:~/app/
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 3. `.env` を作る（**鍵はこの環境で生成する**／§9.5）

```bash
cd ~/app && cp .env.example .env
.venv/bin/python -c "import secrets;print('SECRET_KEY='+secrets.token_urlsafe(48))"
.venv/bin/python -c "from cryptography.fernet import Fernet;print('CREDENTIAL_KEY='+Fernet.generate_key().decode())"
# 出力を .env に貼る。あわせて下記を設定
#   BASE_URL=https://crm.〈example.com〉
#   SESSION_COOKIE_SECURE=1
#   ORG_NAME=〈社名〉
```

**`CREDENTIAL_KEY` は失うと復号できない。控えを別の場所に持つこと。**
**`SECRET_KEY` を入れないとアプリは起動しない**（入れ忘れに気づけなくするより止める）。

## 4. 表を作り、管理者を招待する

```bash
.venv/bin/python scripts/init_db.py --email <あなた>@example.com --name <名前>
# 表示された招待リンクを開いてパスワードを決める
```

デモ用の架空データを入れる場合（**実データは入れない**／§0.3）：

```bash
.venv/bin/python scripts/seed_demo.py            # 何が入るか確認
.venv/bin/python scripts/seed_demo.py --apply
```

## 5. 常駐させる（管理者権限）

```bash
sudo cp /home/〈appuser〉/app/deploy/〈app〉.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now 〈app〉
sudo systemctl status 〈app〉 --no-pager
```

## 6. Caddy に足す（管理者権限）

**既存のサイト設定は変更しない。追記だけ。**

```bash
sudo cp /home/〈appuser〉/app/deploy/Caddyfile.snippet /etc/caddy/conf.d/〈app〉.caddy
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

`conf.d` を読む設定が無い場合は `/etc/caddy/Caddyfile` の末尾に中身を貼ってください。

## 7. 確認

```bash
curl -sS -o /dev/null -w '%{http_code}\n' https://crm.〈example.com〉/login   # 200
cd /home/〈appuser〉/app && .venv/bin/python scripts/nightly_check.py                    # 問題なし
```

## 8. 毎日回すもの（〈appuser〉 の crontab）

```
0 3 * * *  cd /home/〈appuser〉/app && .venv/bin/python scripts/backup.py --dir backups --keep 14
0 4 * * *  cd /home/〈appuser〉/app && .venv/bin/python scripts/nightly_check.py --mail
```

**点検の通知先は `.env` の `ALERT_TO`。**開発環境なのでこちら宛てで構わないが、
**本番は先方の宛先にする**（設計指示書 §9.3）。

## 更新するとき

```bash
# 手元から rsync でコードを送る（data / backups / .env は送らない）
ssh -i ~/.ssh/〈app〉_crm 〈appuser〉@〈サーバーのIP〉 \
  'cd ~/app && .venv/bin/pip install -r requirements.txt && \
   .venv/bin/python scripts/upgrade_db.py --apply'
sudo systemctl restart 〈app〉     # 管理者権限
```

**`upgrade_db.py` は表を作るだけで、既存の表の列は変えない。**
列を足す必要が出たら、控えを取ってから別途手順を作る。
