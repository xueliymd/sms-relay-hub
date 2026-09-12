#!/usr/bin/env sh
set -eu

ENV_FILE=${1:-"$(dirname "$0")/.env"}
mkdir -p "$(dirname "$ENV_FILE")"
touch "$ENV_FILE"

get_value() {
    key=$1
    sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1
}

set_if_empty() {
    key=$1
    value=$2
    current=$(get_value "$key")
    if [ -n "$current" ]; then
        return
    fi
    if grep -q "^${key}=" "$ENV_FILE"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    else
        printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
    fi
}

secret() {
    openssl rand -base64 32 | tr '+/' '-_' | tr -d '=\n'
}

set_if_empty RELAY_SITE :80
set_if_empty RELAY_DOMAIN ""
set_if_empty HTTP_PORT 39300
set_if_empty HTTPS_PORT 39301
set_if_empty APP_PORT 39200
set_if_empty PLATFORM_VERSION 1.1.0
set_if_empty PUBLIC_BASE_URL ""
set_if_empty ADMIN_USERNAME admin
set_if_empty POSTGRES_DB sms_relay
set_if_empty POSTGRES_USER sms_relay
set_if_empty RETENTION_DAYS 7
set_if_empty RETENTION_INTERVAL_SECONDS 3600
set_if_empty OTP_TTL_SECONDS 1800
set_if_empty CLAIMED_OTP_RETENTION_SECONDS 86400
set_if_empty HMAC_SKEW_SECONDS 300
generated_admin_password=""
generated_admin_token=""
if [ -z "$(get_value ADMIN_PASSWORD)" ]; then
    generated_admin_password=$(secret)
    set_if_empty ADMIN_PASSWORD "$generated_admin_password"
fi
if [ -z "$(get_value ADMIN_TOKEN)" ]; then
    generated_admin_token=$(secret)
    set_if_empty ADMIN_TOKEN "$generated_admin_token"
fi
set_if_empty DATA_ENCRYPTION_KEY "$(secret)"
set_if_empty POSTGRES_PASSWORD "$(secret)"

db=$(get_value POSTGRES_DB)
user=$(get_value POSTGRES_USER)
password=$(get_value POSTGRES_PASSWORD)
set_if_empty DATABASE_URL "postgresql+psycopg://${user}:${password}@postgres:5432/${db}"

chmod 600 "$ENV_FILE"
printf 'Wrote %s; existing non-empty credentials were preserved.\n' "$ENV_FILE"
printf 'Configured ports: HTTP=%s, HTTPS=%s, internal app=%s.\n' "$(get_value HTTP_PORT)" "$(get_value HTTPS_PORT)" "$(get_value APP_PORT)"
printf 'Keep RELAY_SITE=:80. Set RELAY_DOMAIN to enable HTTPS while keeping the IP:HTTP_PORT entry.\n'
if [ -n "$generated_admin_password$generated_admin_token" ]; then
    printf 'Generated administrator credentials (store these securely):\n'
    printf '  Username: %s\n' "$(get_value ADMIN_USERNAME)"
    [ -n "$generated_admin_password" ] && printf '  Password: %s\n' "$(get_value ADMIN_PASSWORD)"
    [ -n "$generated_admin_token" ] && printf '  Recovery token: %s\n' "$(get_value ADMIN_TOKEN)"
    printf 'The generated DATA_ENCRYPTION_KEY and database password are in %s.\n' "$ENV_FILE"
fi
