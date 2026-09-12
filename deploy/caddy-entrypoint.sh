#!/bin/sh
set -eu

domain=${RELAY_DOMAIN:-}
if [ -z "$domain" ] && [ "${RELAY_SITE:-:80}" != ":80" ]; then
    domain=$RELAY_SITE
fi

if [ -n "$domain" ]; then
    if ! printf '%s' "$domain" | grep -Eq '^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$'; then
        echo "RELAY_DOMAIN must be a DNS hostname without scheme, path, or port: $domain" >&2
        exit 1
    fi
    sed "s/__RELAY_DOMAIN__/$domain/g" /etc/caddy/Caddyfile.domain > /tmp/Caddyfile
else
    cp /etc/caddy/Caddyfile /tmp/Caddyfile
fi

exec caddy run --config /tmp/Caddyfile --adapter caddyfile
