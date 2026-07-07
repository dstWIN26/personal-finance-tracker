#!/usr/bin/env bash
#
# Start the finance tracker + Cloudflare Tunnel in one go.
#
#   ./start.sh
#
# Brings up the Docker container and the tunnel together. Ctrl-C shuts BOTH down
# cleanly. On unexpected exit (tunnel crashes, etc.) the container is also stopped.
#
set -euo pipefail
cd "$(dirname "$0")"

CONFIG="${CLOUDFLARED_CONFIG:-./cloudflared/config.yml}"

# ── Preflight ─────────────────────────────────────────────────
command -v docker      >/dev/null || { echo "❌ Docker not installed.";                                       exit 1; }
command -v cloudflared >/dev/null || { echo "❌ cloudflared not installed — run ./setup-tunnel.sh first.";    exit 1; }
[[ -f .env      ]] || { echo "❌ .env missing — run: cp .env.example .env && nano .env";                      exit 1; }
[[ -f "$CONFIG" ]] || { echo "❌ Tunnel config missing at $CONFIG — run ./setup-tunnel.sh first.";            exit 1; }

# Pick docker compose command (v1 or v2)
if docker compose version >/dev/null 2>&1; then
    COMPOSE="docker compose"
else
    COMPOSE="docker-compose"
fi

# Pull the hostname out of the config for the success message
HOSTNAME=$(awk '/^[[:space:]]*-[[:space:]]*hostname:/ {print $3; exit}' "$CONFIG")

# ── Start app container (detached) ────────────────────────────
echo "▶ Starting app container..."
$COMPOSE up -d

# ── Cleanup on any exit ───────────────────────────────────────
TUNNEL_PID=""
cleanup() {
    trap - INT TERM EXIT
    echo ""
    echo "▶ Shutting down..."
    if [[ -n "$TUNNEL_PID" ]]; then
        kill "$TUNNEL_PID" 2>/dev/null || true
        wait "$TUNNEL_PID" 2>/dev/null || true
    fi
    $COMPOSE down
    echo "✓ Stopped."
}
trap cleanup INT TERM EXIT

# ── Start tunnel ──────────────────────────────────────────────
echo "▶ Starting Cloudflare Tunnel..."
cloudflared tunnel --config "$CONFIG" run &
TUNNEL_PID=$!

# Give the tunnel a moment to register, then announce
sleep 2
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  ✓ Running"
echo ""
echo "   Public URL:   https://${HOSTNAME:-<your-domain>}"
echo "   App logs:     $COMPOSE logs -f app"
echo "   Tunnel logs:  visible below"
echo ""
echo "   Press Ctrl-C to stop both."
echo "═══════════════════════════════════════════════════════════"
echo ""

# Block until the tunnel exits (Ctrl-C → trap → cleanup)
wait "$TUNNEL_PID"
