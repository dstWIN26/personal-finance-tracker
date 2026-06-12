#!/bin/sh
# Container entrypoint: run the app as a NON-ROOT user (defence-in-depth) while
# still working with bind-mounted volumes that arrive owned by the host user
# (usually root). We start as root only long enough to fix ownership of the two
# persisted state dirs, then drop to the unprivileged `app` user and exec the app.
set -e

for d in /app/data /app/keys; do
    mkdir -p "$d"
    # Best-effort: on some hosts (e.g. Docker Desktop) the bind mount can't be
    # chowned; that's fine, the mount is already writable there.
    chown -R app:app "$d" 2>/dev/null || true
done

# Drop privileges and hand off to the CMD (uvicorn …).
exec gosu app "$@"
