#!/usr/bin/env bash
#
# Lock inbound 80/443 to Cloudflare's IP ranges, so nobody can bypass Cloudflare's
# WAF/rate-limiting/DDoS protection by hitting the origin VPS IP directly.
#
# Uses the DOCKER-USER iptables chain because Docker-published ports BYPASS ufw.
# SSH (22) and all other ports are left untouched. Run as root on the VPS, AFTER
# `docker compose up` (Docker creates DOCKER-USER). Re-runnable / idempotent.
#
#   sudo ./scripts/cloudflare-firewall.sh
#
# Persist across reboots:  apt install iptables-persistent && netfilter-persistent save
#
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }

PORTS="80,443"

lock() {
    local ipt="$1" chain="$2" url="$3"
    # Fresh, dedicated chain each run (idempotent); hook it into DOCKER-USER once.
    "$ipt" -N "$chain" 2>/dev/null || "$ipt" -F "$chain"
    "$ipt" -C DOCKER-USER -j "$chain" 2>/dev/null || "$ipt" -I DOCKER-USER -j "$chain"

    local n=0
    while read -r cidr; do
        [ -n "$cidr" ] || continue
        "$ipt" -A "$chain" -p tcp -m multiport --dports "$PORTS" -s "$cidr" -j RETURN
        n=$((n + 1))
    done < <(curl -fsSL "$url")

    # Anything else aimed at 80/443 is dropped; other ports fall through untouched.
    "$ipt" -A "$chain" -p tcp -m multiport --dports "$PORTS" -j DROP
    echo "  $ipt: allowed $n Cloudflare ranges on $PORTS"
}

lock iptables  FTS_CF  https://www.cloudflare.com/ips-v4
lock ip6tables FTS_CF6 https://www.cloudflare.com/ips-v6

echo "✓ Origin locked to Cloudflare on $PORTS — direct-to-IP bypass blocked. SSH (22) untouched."
echo "  Persist: apt install iptables-persistent && netfilter-persistent save"
