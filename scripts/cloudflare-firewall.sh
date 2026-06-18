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

# Only filter traffic arriving FROM the internet on the WAN NIC. DOCKER-USER is
# traversed for ALL forwarded packets — including containers' OWN outbound
# connections to remote :443 (Trade Republic, FMP, ECB, Let's Encrypt …) — so an
# un-scoped "drop dports 80,443" silently kills every container's HTTPS egress.
# Matching -i $WAN restricts the lock to direct-to-origin-IP hits (which enter on
# $WAN), leaving container egress untouched.
WAN="$(ip route show default 2>/dev/null | awk '/default/{print $5; exit}')"
[ -n "$WAN" ] || { echo "Could not detect WAN interface (ip route show default)." >&2; exit 1; }

lock() {
    local ipt="$1" chain="$2" url="$3"
    # Fresh, dedicated chain each run (idempotent); hook it into DOCKER-USER once.
    "$ipt" -N "$chain" 2>/dev/null || "$ipt" -F "$chain"
    "$ipt" -C DOCKER-USER -j "$chain" 2>/dev/null || "$ipt" -I DOCKER-USER -j "$chain"

    local n=0
    while read -r cidr; do
        [ -n "$cidr" ] || continue
        "$ipt" -A "$chain" -i "$WAN" -p tcp -m multiport --dports "$PORTS" -s "$cidr" -j RETURN
        n=$((n + 1))
    done < <(curl -fsSL "$url")

    # Anything else arriving on $WAN for 80/443 is dropped; container egress and
    # all other ports fall through untouched.
    "$ipt" -A "$chain" -i "$WAN" -p tcp -m multiport --dports "$PORTS" -j DROP
    echo "  $ipt: allowed $n Cloudflare ranges on $PORTS (inbound via $WAN)"
}

lock iptables  FTS_CF  https://www.cloudflare.com/ips-v4
lock ip6tables FTS_CF6 https://www.cloudflare.com/ips-v6

echo "✓ Origin locked to Cloudflare on $PORTS — direct-to-IP bypass blocked. SSH (22) untouched."
echo "  Persist: apt install iptables-persistent && netfilter-persistent save"
