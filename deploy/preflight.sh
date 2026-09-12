#!/usr/bin/env sh
set -eu

ENV_FILE=${1:-"$(dirname "$0")/.env"}
SKIP_DOCKER=${SKIP_DOCKER:-0}
SKIP_DNS=${SKIP_DNS:-0}
COMPOSE_FILE=$(dirname "$0")/compose.yml
errors=0

get_value() {
    key=$1
    sed -n "s/^${key}=//p" "$ENV_FILE" | tail -n 1 | sed 's/[[:space:]]*$//'
}

error() {
    printf 'ERROR: %s\n' "$1" >&2
    errors=$((errors + 1))
}

warning() {
    printf 'WARNING: %s\n' "$1" >&2
}

if [ ! -f "$ENV_FILE" ]; then
    error "env file not found: $ENV_FILE"
else
    for key in ADMIN_USERNAME ADMIN_PASSWORD ADMIN_TOKEN DATA_ENCRYPTION_KEY POSTGRES_PASSWORD DATABASE_URL RELAY_SITE HTTP_PORT HTTPS_PORT APP_PORT; do
        value=$(get_value "$key")
        [ -n "$value" ] || error "$key is missing or empty"
    done
fi

is_port() {
    case "$1" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ "$1" -ge 1 ] && [ "$1" -le 65535 ]
}

http_port=$(get_value HTTP_PORT || true)
https_port=$(get_value HTTPS_PORT || true)
app_port=$(get_value APP_PORT || true)
is_port "$http_port" || error 'HTTP_PORT must be an integer from 1 to 65535'
is_port "$https_port" || error 'HTTPS_PORT must be an integer from 1 to 65535'
is_port "$app_port" || error 'APP_PORT must be an integer from 1 to 65535'
[ -n "$http_port" ] && [ "$http_port" != "$https_port" ] || error 'HTTP_PORT and HTTPS_PORT must be different'
if [ -n "$app_port" ] && { [ "$app_port" = "$http_port" ] || [ "$app_port" = "$https_port" ]; }; then
    warning 'APP_PORT is an internal container port; it matches a host port. This is valid, but a different value is easier to diagnose.'
fi

site=$(get_value RELAY_SITE || true)
relay_domain=$(get_value RELAY_DOMAIN || true)
site_host=''
case "$site" in
    :*)
        site_port=${site#:}
        is_port "$site_port" || error 'RELAY_SITE listen port is invalid'
        [ -n "$relay_domain" ] || warning 'RELAY_DOMAIN is empty; only the HTTP/IP entry is enabled.'
        ;;
    '')
        ;;
    *[/:]*)
        error 'RELAY_SITE must be :PORT or a DNS hostname without scheme/path'
        ;;
    *)
        [ -n "$relay_domain" ] || warning 'RELAY_SITE contains a domain for backward compatibility; prefer RELAY_SITE=:80 plus RELAY_DOMAIN.'
        ;;
esac

if [ -n "$relay_domain" ]; then
    case "$relay_domain" in
        *[!A-Za-z0-9.-]*|.*|*.|*..*)
            error 'RELAY_DOMAIN must be a DNS hostname without scheme, path, or port'
            ;;
        *)
            site_host=$relay_domain
            ;;
    esac
    [ "$site" = ':80' ] || error 'RELAY_SITE must stay :80 in dual mode; put the DNS name in RELAY_DOMAIN'
    [ "$https_port" = '443' ] || warning "HTTPS_PORT is $https_port; the domain URL must include this port unless it is 443."
fi

public_base_url=$(get_value PUBLIC_BASE_URL || true)
if [ -z "$public_base_url" ]; then
    warning 'PUBLIC_BASE_URL is empty; clients must use the manually configured server address.'
else
    case "$public_base_url" in
        http://*|https://*) ;;
        *) error 'PUBLIC_BASE_URL must start with http:// or https://' ;;
    esac
    # Match the shape on the authority only, after dropping the scheme. Every
    # origin contains the double slash of `scheme://`, so the previous path glob
    # ran over the whole value and matched even `https://example.com`, which made
    # preflight exit 1 for any non-empty PUBLIC_BASE_URL. Trailing slashes are
    # stripped first because backend/app/core/config.py normalises them away
    # before validating. backend/tests/test_deploy.py asserts the old glob never
    # comes back, so do not reproduce it in a comment either.
    base_authority=${public_base_url#*://}
    while :; do
        case "$base_authority" in
            */) base_authority=${base_authority%/} ;;
            *) break ;;
        esac
    done
    case "$base_authority" in
        ''|*/*|*'@'*|*'?'*|*'#'*)
            error 'PUBLIC_BASE_URL must be an origin without credentials, path, query, or fragment'
            ;;
    esac
    if [ -n "$site_host" ] && [ "${base_authority%%:*}" != "$site_host" ]; then
        error "PUBLIC_BASE_URL host does not match RELAY_DOMAIN=$site_host"
    fi
    case "$public_base_url" in
        http://*) warning 'PUBLIC_BASE_URL uses HTTP; use HTTPS for public iPhone and admin traffic.' ;;
        https://*)
            [ -n "$relay_domain" ] || warning 'PUBLIC_BASE_URL is HTTPS but RELAY_DOMAIN is empty, so Caddy only serves the HTTP/IP entry; set RELAY_DOMAIN or point PUBLIC_BASE_URL at the address clients can actually reach.'
            ;;
    esac
fi

if [ -n "$site_host" ] && [ "$SKIP_DNS" != '1' ] && command -v getent >/dev/null 2>&1; then
    getent ahosts "$site_host" >/dev/null 2>&1 || warning "DNS did not resolve $site_host from this machine"
fi

if [ "$SKIP_DOCKER" != '1' ]; then
    if ! command -v docker >/dev/null 2>&1; then
        warning 'docker command is not available; skipped docker compose config validation'
    else
        if ! docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" config --quiet >/dev/null 2>&1; then
            error 'docker compose config validation failed'
        fi

        # Single-instance guard. Two Compose projects on one compose file mean two
        # databases, and the entry point then serves an empty stack while the real
        # data stays in the other volume. docker ps renders .Labels as a single
        # comma-joined string, so `index .Labels "key"` is a template error that
        # silently hides every project; `.Label "key"` is the per-label accessor.
        deploy_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
        if compose_rows=$(docker ps -a --filter 'label=com.docker.compose.project' --format '{{.Label "com.docker.compose.project"}}|{{.Label "com.docker.compose.service"}}|{{.Label "com.docker.compose.project.config_files"}}|{{.Label "com.docker.compose.project.working_dir"}}' 2>&1); then
            install_projects=$(printf '%s\n' "$compose_rows" |
                awk -F'|' -v cfg="$deploy_dir/compose.yml" -v wd="$deploy_dir" '
                    $1 == "" { next }
                    $2 != "app" && $2 != "caddy" { next }
                    index($3, cfg) > 0 || $4 == wd { print $1 }
                ' |
                sort -u)
            install_project_count=$(printf '%s\n' "$install_projects" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')
            env_project=$(get_value COMPOSE_PROJECT_NAME || true)
            if [ "$install_project_count" -gt 1 ]; then
                error "multiple Compose projects use $deploy_dir/compose.yml: $(printf '%s' "$install_projects" | tr '\n' ' '); keep the project that holds real data and remove only the verified empty project"
            elif [ "$install_project_count" -eq 1 ]; then
                printf 'Compose project in use: %s\n' "$install_projects"
                if [ -n "$env_project" ] && [ "$env_project" != "$install_projects" ]; then
                    error "COMPOSE_PROJECT_NAME=$env_project does not match the running Compose project $install_projects; updating under the wrong project name creates a second empty database"
                fi
            elif [ -n "$env_project" ]; then
                warning "COMPOSE_PROJECT_NAME=$env_project is set by hand; leave it empty so Compose keeps the existing project name"
            fi

            postgres_volumes=$(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -E '_postgres_data$' | sort -u || true)
            postgres_volume_count=$(printf '%s\n' "$postgres_volumes" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' ')
            if [ "$postgres_volume_count" -gt 1 ]; then
                warning "more than one database volume exists: $(printf '%s' "$postgres_volumes" | tr '\n' ' '); confirm which volume holds real data before updating"
            fi
        else
            warning 'could not read Compose container labels; skipped the duplicate stack check'
            warning "$compose_rows"
        fi
    fi
fi

if command -v ss >/dev/null 2>&1; then
    for port in "$http_port" "$https_port"; do
        if is_port "$port" && ss -ltnH 2>/dev/null | awk -v port=":$port" '$4 ~ port"$" {found=1} END {exit !found}'; then
            warning "host port $port is already listening; verify it belongs to this Compose instance"
        fi
    done
fi

[ "$errors" -eq 0 ] || exit 1
printf 'Preflight passed: %s\n' "$ENV_FILE"
printf 'Ports: HTTP=%s HTTPS=%s APP=%s\n' "$http_port" "$https_port" "$app_port"
# Keep this an if block: a trailing `[ -n "$x" ] && printf ...` makes the script
# exit 1 whenever no domain is configured, even though every check passed.
if [ -n "$site_host" ]; then
    printf 'HTTPS hostname: %s\n' "$site_host"
fi
exit 0
