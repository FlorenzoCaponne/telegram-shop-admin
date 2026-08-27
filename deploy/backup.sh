#!/usr/bin/env bash
# Бэкап БД и медиа. Запуск по cron: 0 4 * * * /opt/tgshop/deploy/backup.sh >> /var/log/tgshop-backup.log 2>&1
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"

mkdir -p "$BACKUP_DIR"
cd "$PROJECT_DIR"

if docker compose ps postgres >/dev/null 2>&1; then
  echo "[backup] dump via docker compose"
  docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-tgshop}" "${POSTGRES_DB:-tgshop}" \
    | gzip > "$BACKUP_DIR/db-$STAMP.sql.gz"
else
  echo "[backup] dump via local pg_dump"
  pg_dump "${DATABASE_URL_SYNC:?set DATABASE_URL_SYNC}" | gzip > "$BACKUP_DIR/db-$STAMP.sql.gz"
fi

if [ -d media ]; then
  tar -czf "$BACKUP_DIR/media-$STAMP.tar.gz" media
fi

find "$BACKUP_DIR" -type f -name '*.gz' -mtime "+$KEEP_DAYS" -delete
echo "[backup] done: $BACKUP_DIR/db-$STAMP.sql.gz"
