#!/usr/bin/env bash
# Быстрый старт на локальном ПК (polling, SQLite или локальный Postgres).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "ℹ Создан .env из .env.example — впишите BOT_TOKEN и ключи, затем запустите скрипт снова."
  exit 1
fi

if [ ! -d .venv ]; then
  echo "[1/3] создаю виртуальное окружение"
  python3 -m venv .venv
fi

source .venv/bin/activate
echo "[2/3] зависимости"
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "[3/3] миграции и запуск"
alembic upgrade head
exec uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
