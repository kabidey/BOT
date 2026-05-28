#!/usr/bin/env bash
# Phase 28 — nightly mongodump of mackertich_prod
# Writes a single gzipped archive to /var/backups/mongodump/dump-YYYYMMDD.gz
# Retention: 14 days.
set -euo pipefail

DATE=$(date +%Y%m%d)
HOST_OUT=/var/backups/mongodump
mkdir -p "$HOST_OUT"
ARCHIVE="$HOST_OUT/dump-${DATE}.gz"

# Dump straight to a host file via the container's stdout to avoid
# needing to bind-mount /var/backups into the mongo container.
docker exec mackertich-mongo mongodump \
    --db mackertich_prod \
    --gzip --archive \
    > "$ARCHIVE"

# Sanity: archive must be > 1 MB (preview was 36 MB, prod should be similar)
SIZE=$(stat -c '%s' "$ARCHIVE")
if [ "$SIZE" -lt 1048576 ]; then
    echo "[$(date -Iseconds)] WARN: archive suspiciously small (${SIZE} bytes)" >&2
fi

# Retain 14 days
find "$HOST_OUT" -name 'dump-*.gz' -type f -mtime +14 -delete

echo "[$(date -Iseconds)] mongodump ok: $ARCHIVE ($(numfmt --to=iec $SIZE 2>/dev/null || echo ${SIZE}b))"
