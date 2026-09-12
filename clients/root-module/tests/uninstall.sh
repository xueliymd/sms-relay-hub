#!/bin/sh

set -eu

TEST_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/sms-relay-uninstall.XXXXXX")
cleanup() {
    rm -rf "$TEST_ROOT"
}
trap cleanup EXIT HUP INT TERM

STATE_DIR=$TEST_ROOT/state
LOCK_FILE=$TEST_ROOT/lock
LOCK_DIR=$LOCK_FILE.d
mkdir -p "$STATE_DIR/spool" "$STATE_DIR/run"
printf '%s' old-config > "$STATE_DIR/config.json"
: > "$STATE_DIR/disabled"
: > "$LOCK_FILE"
mkdir -p "$LOCK_DIR"
: > "$LOCK_DIR/pid"

cat > "$STATE_DIR/run/worker.pid" <<EOF
$$
EOF
cat > "$STATE_DIR/run/web.pid" <<EOF
$$
EOF

SMS_RELAY_STATE_DIR="$STATE_DIR" \
SMS_RELAY_LOCK_FILE="$LOCK_FILE" \
SMS_RELAY_ALLOW_CUSTOM_STATE=1 \
    sh "$(CDPATH= cd "$(dirname "$0")/.." && pwd)/uninstall.sh"

[ ! -e "$STATE_DIR" ] || { printf '%s\n' 'FAIL uninstall-state-retained' >&2; exit 1; }
[ ! -e "$LOCK_FILE" ] || { printf '%s\n' 'FAIL uninstall-lock-retained' >&2; exit 1; }
[ ! -e "$LOCK_DIR" ] || { printf '%s\n' 'FAIL uninstall-directory-lock-retained' >&2; exit 1; }
printf '%s\n' 'PASS behavior=uninstall-cleans-state'
