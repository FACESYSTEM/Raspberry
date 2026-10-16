#!/usr/bin/env bash
# 起動を1か所にまとめる（設計指示書 §9.2）。
# systemd から呼ぶが、systemd が使えない環境でも直接叩けば動く。
set -euo pipefail

cd "$(dirname "$0")/.."

# .env は app/config.py の load_dotenv が読む。ここでは読まない（二重に読まない）
PORT="${PORT:-8010}"
WORKERS="${WORKERS:-2}"

exec .venv/bin/gunicorn \
  --workers "$WORKERS" \
  --bind "127.0.0.1:${PORT}" \
  --timeout 60 \
  --access-logfile - \
  --error-logfile - \
  run:app
