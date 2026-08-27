#!/usr/bin/env bash
# Обновление на VPS без простоя: бэкап -> сборка -> миграции -> рестарт -> health.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "[1/5] бэкап"
bash deploy/backup.sh || echo "⚠ бэкап не выполнен, продолжаю"

if [ -d .git ]; then
  echo "[2/5] git pull"
  git pull --ff-only
fi

echo "[3/5] сборка образа"
docker compose build app

echo "[4/5] миграции и рестарт"
docker compose run --rm migrate
docker compose up -d app nginx

echo "[5/5] health-check"
for i in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:8000/health >/dev/null; then
    echo "✅ приложение здорово"
    docker image prune -f >/dev/null 2>&1 || true
    exit 0
  fi
  sleep 3
done

echo "❌ health-check не прошёл, логи:"
docker compose logs --tail 80 app
exit 1
