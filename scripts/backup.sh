#!/usr/bin/env bash
#
# Encrypted backup of the only irreplaceable state: data/ (SQLite DB — positions,
# transactions, sessions, ENROLLED PASSKEYS) and keys/ (Trade Republic device
# keyfile). Run on the VPS host (it operates on the bind-mounted dirs, not the
# container). Schedule via cron — see DEPLOY.md.
#
#   BACKUP_PASSPHRASE   (required)  AES-256 passphrase. Store it OFF the server.
#   BACKUP_DIR          (default backups)   local snapshot dir
#   BACKUP_KEEP         (default 14)        how many local snapshots to retain
#   BACKUP_SCP_DEST     (optional) e.g. user@host:/srv/fts-backups
#   BACKUP_RCLONE_DEST  (optional) e.g. b2:my-bucket/fts   (needs rclone configured)
#
set -euo pipefail
cd "$(dirname "$0")/.."          # repo root

: "${BACKUP_PASSPHRASE:?Set BACKUP_PASSPHRASE — the AES-256 passphrase (keep it off this server)}"
DEST_DIR="${BACKUP_DIR:-backups}"
KEEP="${BACKUP_KEEP:-14}"
mkdir -p "$DEST_DIR"

# Only back up what exists (keys/ may be absent until TR is linked).
targets=()
[ -d data ] && targets+=("data")
[ -d keys ] && targets+=("keys")
if [ ${#targets[@]} -eq 0 ]; then
    echo "Nothing to back up (no data/ or keys/ dir)." >&2
    exit 1
fi

ts="$(date +%Y%m%d-%H%M%S)"
archive="$DEST_DIR/fts-backup-$ts.tar.gz.enc"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

tar -czf "$tmp" "${targets[@]}"
openssl enc -aes-256-cbc -pbkdf2 -salt -in "$tmp" -out "$archive" -pass env:BACKUP_PASSPHRASE
chmod 600 "$archive"
echo "✓ Wrote $archive ($(du -h "$archive" | cut -f1))"

# Retention: keep the newest $KEEP encrypted snapshots.
ls -1t "$DEST_DIR"/fts-backup-*.tar.gz.enc 2>/dev/null | tail -n +"$((KEEP + 1))" | xargs -r rm -f

# Optional off-box copies (configure either / both).
if [ -n "${BACKUP_SCP_DEST:-}" ]; then
    scp -q "$archive" "$BACKUP_SCP_DEST/" && echo "✓ Copied to $BACKUP_SCP_DEST"
fi
if [ -n "${BACKUP_RCLONE_DEST:-}" ]; then
    rclone copy "$archive" "$BACKUP_RCLONE_DEST" && echo "✓ Copied to rclone:$BACKUP_RCLONE_DEST"
fi
