#!/usr/bin/env bash
# Daily Postgres backup with retention.
#   Uses the standard PG* variables (PGHOST, PGUSER, PGPASSWORD, PGDATABASE).
#   BACKUP_DIR        where dumps go (default /backups)
#   BACKUP_KEEP_DAYS  delete dumps older than this (default 14)
#   BACKUP_TIME_UTC   daily run time, HH:MM in UTC (default 10:15, which is 03:15 in Los Angeles in summer)
#   BACKUP_ONCE=1     take 1 backup now and exit (manual runs and tests)
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
BACKUP_TIME_UTC="${BACKUP_TIME_UTC:-10:15}"

backup_now() {
  mkdir -p "$BACKUP_DIR"
  local stamp file
  stamp="$(date -u +%Y-%m-%dT%H%M%SZ)"
  file="$BACKUP_DIR/bodyshop-$stamp.dump"
  pg_dump --format=custom --no-owner --file="$file.partial"
  # A dump that pg_restore cannot list is not a backup.
  pg_restore --list "$file.partial" > /dev/null
  mv "$file.partial" "$file"
  find "$BACKUP_DIR" -name 'bodyshop-*.dump' -type f -mtime +"$BACKUP_KEEP_DAYS" -delete
  echo "backup ok: $file ($(du -h "$file" | cut -f1))"
}

if [[ "${BACKUP_ONCE:-0}" == "1" ]]; then
  backup_now
  exit 0
fi

while true; do
  now=$(date -u +%s)
  next=$(date -u -d "today $BACKUP_TIME_UTC" +%s)
  if (( next <= now )); then
    next=$(date -u -d "tomorrow $BACKUP_TIME_UTC" +%s)
  fi
  sleep $(( next - now ))
  backup_now || echo "backup FAILED at $(date -u)" >&2
done
