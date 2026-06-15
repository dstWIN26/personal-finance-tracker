FROM python:3.11-slim

WORKDIR /app

# gosu = tiny, su-exec-style privilege-drop tool. The entrypoint uses it to fix
# volume ownership as root, then run the app as a non-root user. We copy the static
# binary from its official (multi-arch) image over HTTPS instead of apt-installing
# it, so the build needs no Debian HTTP mirror (port 80) — only the registry (443),
# which is far more reliable behind restrictive egress / on some VPS networks.
COPY --from=tianon/gosu:1.17 /usr/local/bin/gosu /usr/local/bin/gosu
RUN useradd --system --uid 10001 --user-group --home-dir /app app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY frontend/ ./frontend/
COPY scripts/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

# Liveness probe (no curl in slim image → use stdlib urllib). Compose restarts the
# container if this fails repeatedly.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/healthz', timeout=4).status==200 else 1)"]

# Entrypoint fixes volume perms as root, then drops to the non-root `app` user.
ENTRYPOINT ["docker-entrypoint.sh"]

# Behind Caddy on the internal Docker network: trust X-Forwarded-* only from
# loopback + the private (RFC1918) Docker bridge ranges, NOT the whole internet
# (was "*"). The app port is unpublished, so only Caddy can reach uvicorn anyway —
# this is defence-in-depth so a forwarded client IP can't be spoofed if that ever
# changes. CF-Connecting-IP trust is enforced separately by the origin firewall.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"]
