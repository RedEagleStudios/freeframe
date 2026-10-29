#!/usr/bin/env bash
# Accept new connections to ports 80/443 from Cloudflare's ranges only.
#
# For docker-compose.cloudflare.yml: with the origin closed to everyone else,
# nobody can bypass Cloudflare, and CLIENT_IP_HEADER=cf-connecting-ip cannot be
# forged. Idempotent; run at boot after Docker (see
# deploy/systemd/freeframe-cloudflare-firewall.service).
#
# IPv4: Docker DNATs published ports, so the traffic crosses FORWARD, where
#       Docker leaves the DOCKER-USER chain for rules like these (ufw and the
#       INPUT chain never see it).
# IPv6: with no IPv6 on the Docker bridge, docker-proxy listens on [::]:80/443
#       itself, so that traffic arrives on INPUT.
# Only packets coming IN on the external interface are matched, so containers
# can still reach HTTPS services (R2, Discord) on the internet.
set -euo pipefail

CACHE_DIR=/etc/freeframe
EXT_IF=${EXT_IF:-$(ip route show default | awk '{print $5; exit}')}
[ -n "$EXT_IF" ] || { echo "no default route interface; set EXT_IF" >&2; exit 1; }

mkdir -p "$CACHE_DIR"
fetch() {  # fetch <v4|v6>: refresh the cached list, keep the old one if the fetch fails
  local tmp
  tmp=$(mktemp)
  if curl -fsS --max-time 15 "https://www.cloudflare.com/ips-$1" -o "$tmp" && grep -q / "$tmp"; then
    mv "$tmp" "$CACHE_DIR/cloudflare-ips-$1"
  else
    rm -f "$tmp"
    echo "could not refresh Cloudflare $1 ranges; using the cached list" >&2
  fi
  # An empty list would turn the chain into "drop everything": refuse instead.
  grep -q / "$CACHE_DIR/cloudflare-ips-$1" 2>/dev/null || { echo "no Cloudflare $1 ranges available" >&2; exit 1; }
}
fetch v4
fetch v6

apply() {  # apply <iptables|ip6tables> <chain> <hook> <ranges file>
  local ipt=$1 chain=$2 hook=$3 ranges=$4
  $ipt -w -N "$chain" 2>/dev/null || $ipt -w -F "$chain"
  while read -r cidr; do
    [ -n "$cidr" ] && $ipt -w -A "$chain" -s "$cidr" -j RETURN
  done < "$ranges"
  $ipt -w -A "$chain" -j DROP
  local match=(-i "$EXT_IF" -p tcp -m multiport --dports 80,443 -m conntrack --ctstate NEW -j "$chain")
  $ipt -w -C "$hook" "${match[@]}" 2>/dev/null || $ipt -w -I "$hook" 1 "${match[@]}"
}

iptables -w -N DOCKER-USER 2>/dev/null || true
apply iptables FF-CLOUDFLARE DOCKER-USER "$CACHE_DIR/cloudflare-ips-v4"
apply ip6tables FF-CLOUDFLARE6 INPUT "$CACHE_DIR/cloudflare-ips-v6"
echo "80/443 on $EXT_IF limited to Cloudflare ($(wc -l < "$CACHE_DIR/cloudflare-ips-v4") v4, $(wc -l < "$CACHE_DIR/cloudflare-ips-v6") v6 ranges)"
