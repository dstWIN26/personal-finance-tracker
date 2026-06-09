#!/usr/bin/env bash
#
# Restore data/ and keys/ from an encrypted backup produced by backup.sh.
#
#   BACKUP_PASSPHRASE=...  ./scripts/restore.sh  backups/fts-backup-YYYYMMDD-HHMMSS.tar.gz.enc
#
# OVERWRITES the current data/ and keys/. Stop the app first (docker compose down).
#
set -euo pipefail
cd "$(dirname "$0")/.."          # repo root

: "${BACKUP_PASSPHRASE:?Set BACKUP_PASSPHRASE — the passphrase the backup was encrypted with}"
archive="${1:?Usage: restore.sh <path-to-backup.tar.gz.enc>}"
[ -f "$archive" ] || { echo "No such file: $archive" >&2; exit 1; }

echo "This will OVERWRITE ./data and ./keys from:"
echo "  $archive"
read -r -p "Type 'yes' to continue: " confirm
[ "$confirm" = "yes" ] || { echo "Aborted."; exit 1; }

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
openssl enc -d -aes-256-cbc -pbkdf2 -in "$archive" -out "$tmp" -pass env:BACKUP_PASSPHRASE
tar -xzf "$tmp" -C .

echo "✓ Restored. Start the app again:  docker compose up -d"
