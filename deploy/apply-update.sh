#!/bin/sh

set -eu
umask 077

TARGET=${1:-}
if [ -z "$TARGET" ]; then
    printf '%s\n' 'usage: sh deploy/apply-update.sh /absolute/current/install/path [backup-root]' >&2
    exit 2
fi
case "$TARGET" in
    /*) ;;
    *) printf '%s\n' 'target path must be absolute' >&2; exit 2 ;;
esac
if [ "$TARGET" = / ] || [ ! -f "$TARGET/deploy/compose.yml" ] || [ ! -f "$TARGET/deploy/.env" ]; then
    printf '%s\n' 'target is not a valid Relay Hub installation' >&2
    exit 2
fi
TARGET=$(CDPATH='' cd "$TARGET" && pwd)

SCRIPT_DIR=$(CDPATH='' cd "$(dirname "$0")" && pwd)
SOURCE_ROOT=$(CDPATH='' cd "$SCRIPT_DIR/.." && pwd)
if [ ! -f "$SOURCE_ROOT/web/public/assets/releases/release-catalog.json" ]; then
    printf '%s\n' 'update package is missing the filtered release catalog' >&2
    exit 2
fi
if grep -Eq '"platform"[[:space:]]*:[[:space:]]*"deploy"' "$SOURCE_ROOT/web/public/assets/releases/release-catalog.json"; then
    printf '%s\n' 'update package release catalog contains container deployment packages' >&2
    exit 2
fi
if [ -d "$SOURCE_ROOT/web/public/assets/releases/deploy" ]; then
    printf '%s\n' 'update package contains a container deployment release directory' >&2
    exit 2
fi
case "$SOURCE_ROOT" in
    "$TARGET"|"$TARGET"/*) printf '%s\n' 'extract the update package outside the active installation' >&2; exit 2 ;;
esac

BACKUP_ROOT=${2:-"$(dirname "$TARGET")/relay-update-history"}
case "$BACKUP_ROOT" in
    "$TARGET"|"$TARGET"/*) printf '%s\n' 'backup root must be outside the active installation' >&2; exit 2 ;;
esac

compose() {
    docker compose -p "$COMPOSE_PROJECT_NAME" --env-file "$TARGET/deploy/.env" -f "$TARGET/deploy/compose.yml" "$@"
}
write_env_value() {
    key=$1
    value=$2
    if grep -q "^${key}=" "$TARGET/deploy/.env"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "$TARGET/deploy/.env"
    else
        printf '%s=%s\n' "$key" "$value" >> "$TARGET/deploy/.env"
    fi
}

# Older installations may pin the previous 30-minute OTP default in .env.
# Keep values chosen by the operator, but migrate the historical default and
# add settings introduced by newer releases so the running container actually
# receives the same defaults as a fresh installation.
migrate_env_defaults() {
    otp_ttl=$(env_file_value OTP_TTL_SECONDS)
    if [ -z "$otp_ttl" ] || [ "$otp_ttl" = "1800" ]; then
        write_env_value OTP_TTL_SECONDS 600
    fi
    if [ -z "$(env_file_value DEVICE_COMMAND_PENDING_TTL_SECONDS)" ]; then
        write_env_value DEVICE_COMMAND_PENDING_TTL_SECONDS 600
    fi
    if [ -z "$(env_file_value DEVICE_COMMAND_RESULT_TTL_SECONDS)" ]; then
        write_env_value DEVICE_COMMAND_RESULT_TTL_SECONDS 1800
    fi
}

# Read one key from .env without sourcing the file, so generated secrets can
# never be executed as shell code during an update.
env_file_value() {
    key=$1
    sed -n "s/^[[:space:]]*${key}=//p" "$TARGET/deploy/.env" |
        tail -n 1 |
        tr -d '\r' |
        sed -e 's/^"\(.*\)"$/\1/'
}

# docker ps renders .Labels as a single comma-joined string, so
# `index .Labels "key"` is a template error that prints nothing and hides every
# existing project. `.Label "key"` is the documented per-label accessor.
compose_container_rows() {
    docker ps -a \
        --filter 'label=com.docker.compose.project' \
        --format '{{.Label "com.docker.compose.project"}}|{{.Label "com.docker.compose.service"}}|{{.Label "com.docker.compose.project.config_files"}}|{{.Label "com.docker.compose.project.working_dir"}}'
}

compose_projects_with_relay_services() {
    printf '%s\n' "$compose_rows" |
        awk -F'|' '$1 != "" && ($2 == "app" || $2 == "caddy") { print $1 }' |
        sort -u
}

# A project belongs to this installation when its config_files label points at
# this compose file, or when its working_dir label is this deploy directory.
# The second signal keeps detection working when the stack was started with a
# relative -f path.
compose_projects_for_install() {
    printf '%s\n' "$compose_rows" |
        awk -F'|' -v cfg="$TARGET/deploy/compose.yml" -v wd="$TARGET/deploy" '
            $1 == "" { next }
            $2 != "app" && $2 != "caddy" { next }
            index($3, cfg) > 0 || $4 == wd { print $1 }
        ' |
        sort -u
}

count_names() {
    printf '%s\n' "$1" | sed '/^[[:space:]]*$/d' | wc -l | tr -d ' '
}

compose_up_postgres() {
    compose up -d --wait postgres
}
if ! compose_rows=$(compose_container_rows 2>&1); then
    printf '%s\n' 'failed to query Docker for existing Compose projects:' >&2
    printf '%s\n' "$compose_rows" >&2
    exit 2
fi

install_projects=$(compose_projects_for_install)
install_project_count=$(count_names "$install_projects")
override_project=${RELAY_COMPOSE_PROJECT:-}

if [ -n "$override_project" ]; then
    relay_projects=$(compose_projects_with_relay_services)
    if printf '%s\n' "$relay_projects" | grep -Fqx -- "$override_project"; then
        COMPOSE_PROJECT_NAME="$override_project"
        printf '%s\n' "using Compose project from RELAY_COMPOSE_PROJECT: $COMPOSE_PROJECT_NAME"
    else
        printf '%s\n' "RELAY_COMPOSE_PROJECT=$override_project has no app or caddy container on this host" >&2
        printf '%s\n' 'existing Compose projects with app or caddy containers:' >&2
        printf '%s\n' "${relay_projects:-<none>}" >&2
        exit 2
    fi
elif [ "$install_project_count" -gt 1 ]; then
    printf '%s\n' "multiple running Compose projects use $TARGET/deploy/compose.yml:" >&2
    printf '%s\n' "$install_projects" >&2
    printf '%s\n' 'refusing to update because choosing the wrong project can expose an empty database; keep the project with real data and remove only the verified empty project first' >&2
    exit 2
elif [ "$install_project_count" -eq 1 ]; then
    COMPOSE_PROJECT_NAME="$install_projects"
    printf '%s\n' "using existing Compose project: $COMPOSE_PROJECT_NAME"
else
    printf '%s\n' 'no existing app or caddy Compose project was detected for this compose file; start the correct stack first, then rerun this update' >&2
    printf '%s\n' "expected a config_files label containing $TARGET/deploy/compose.yml or a working_dir label equal to $TARGET/deploy" >&2
    printf '%s\n' 'compose containers visible to Docker (project|service|config_files|working_dir):' >&2
    printf '%s\n' "${compose_rows:-<none>}" >&2
    printf '%s\n' 'if the real stack already runs under a known project name, rerun with RELAY_COMPOSE_PROJECT=<project> in front of this command' >&2
    exit 2
fi
case "$COMPOSE_PROJECT_NAME" in
    ''|*[!A-Za-z0-9_-]*)
        printf '%s\n' 'invalid Compose project name detected from Docker labels' >&2
        exit 2
        ;;
esac
export COMPOSE_PROJECT_NAME
POSTGRES_USER=$(env_file_value POSTGRES_USER)
POSTGRES_DB=$(env_file_value POSTGRES_DB)
POSTGRES_USER=${POSTGRES_USER:-sms_relay}
POSTGRES_DB=${POSTGRES_DB:-sms_relay}

if [ -n "${RELAY_UPDATE_DETECT_ONLY:-}" ]; then
    printf '%s\n' "detect-only: compose project $COMPOSE_PROJECT_NAME"
    printf '%s\n' "detect-only: postgres user $POSTGRES_USER, database $POSTGRES_DB"
    printf '%s\n' 'detect-only: no backup, no file replacement and no container change was performed'
    exit 0
fi
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BACKUP_DIR="$BACKUP_ROOT/relay-backup-$STAMP"
mkdir -p "$BACKUP_DIR"

tar -czf "$BACKUP_DIR/source.tar.gz" -C "$TARGET" .
cp "$TARGET/deploy/.env" "$BACKUP_DIR/deploy.env"
if [ -d "$TARGET/web/public/assets/releases" ]; then
    cp -a "$TARGET/web/public/assets/releases" "$BACKUP_DIR/release-store"
fi
compose_up_postgres
compose exec -T postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$BACKUP_DIR/database.dump"

rollback() {
    status=$?
    trap - EXIT HUP INT TERM
    if [ "$status" -ne 0 ]; then
        printf '%s\n' 'update failed; restoring previous source, configuration and database' >&2
        compose stop app caddy || true
        rm -rf "$TARGET/backend" "$TARGET/clients" "$TARGET/deploy" "$TARGET/web"
        rm -f "$TARGET/README.md" "$TARGET/.dockerignore"
        tar -xzf "$BACKUP_DIR/source.tar.gz" -C "$TARGET"
        # The container entrypoint runs `alembic upgrade head` *before* the health
        # check, so a failed update has already committed any new migrations.
        # Restoring only the source leaves the database ahead of the revision the
        # restored code knows; the app then crash-loops with
        # "Can't locate revision identified by ...". Put the pre-update dump back
        # from the same backup so source and schema always move back together.
        if compose_up_postgres; then
            if compose exec -T postgres pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
                --clean --if-exists --no-owner < "$BACKUP_DIR/database.dump"; then
                printf '%s\n' 'database restored from the pre-update backup' >&2
            else
                printf '%s\n' "WARNING: database restore failed; recover manually from $BACKUP_DIR/database.dump" >&2
            fi
        else
            printf '%s\n' "WARNING: postgres did not start; recover the database manually from $BACKUP_DIR/database.dump" >&2
        fi
        compose up --build -d || true
    fi
    exit "$status"
}
trap rollback EXIT HUP INT TERM
cp "$SOURCE_ROOT/deploy/compose.yml" "$TARGET/deploy/compose.yml"
compose stop app caddy
rm -rf "$TARGET/backend" "$TARGET/clients" "$TARGET/deploy" "$TARGET/web"
rm -f "$TARGET/README.md" "$TARGET/.dockerignore"
cp -a "$SOURCE_ROOT/backend" "$SOURCE_ROOT/clients" "$SOURCE_ROOT/deploy" "$SOURCE_ROOT/web" "$TARGET/"
cp "$SOURCE_ROOT/README.md" "$SOURCE_ROOT/.dockerignore" "$TARGET/"
cp "$BACKUP_DIR/deploy.env" "$TARGET/deploy/.env"
write_env_value COMPOSE_PROJECT_NAME "$COMPOSE_PROJECT_NAME"
migrate_env_defaults

# Layer caching is content-addressed: COPY backend/ and COPY web/ invalidate
# whenever sources change, so updates always rebuild what matters while
# unchanged base/dependency layers stay reusable. --no-cache forced entirely
# new layers on every update and grew the build cache by ~1-2 GB each time;
# run it manually only when a cache defect is suspected, then builder prune.
compose build app
compose up -d
compose ps
trap - EXIT HUP INT TERM
printf '%s\n' "update complete; backup retained at $BACKUP_DIR"
