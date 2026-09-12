#!/bin/sh

set -eu
umask 077

TEST_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
MODULE_ROOT=$(CDPATH= cd "$TEST_DIR/.." && pwd)
WATCHER=$MODULE_ROOT/system/bin/sms-relay-watch
TEMP_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/sms-relay-watch.XXXXXX")
trap 'rm -rf "$TEMP_ROOT"' EXIT HUP INT TERM

fail() {
    printf '%s\n' "FAIL watcher_contract=$1" >&2
    exit 1
}

[ -x "$WATCHER" ] || fail watcher-missing

mkdir -p "$TEMP_ROOT/bin" "$TEMP_ROOT/user" "$TEMP_ROOT/user-de"
touch "$TEMP_ROOT/user/mmssms.db" "$TEMP_ROOT/user/mmssms.db-wal" "$TEMP_ROOT/user/mmssms.db-shm"
touch "$TEMP_ROOT/user/sms.db" "$TEMP_ROOT/user/sms.db-wal" "$TEMP_ROOT/user/sms.db-shm"
cat > "$TEMP_ROOT/bin/wake" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod 700 "$TEMP_ROOT/bin/wake"
cat > "$TEMP_ROOT/bin/inotifyd" <<'EOF'
#!/bin/sh
printf '%s\n' "$@" > "$WATCHER_ARGUMENTS"
EOF
chmod 700 "$TEMP_ROOT/bin/inotifyd"

WATCHER_ARGUMENTS=$TEMP_ROOT/arguments \
SMS_RELAY_INOTIFYD=$TEMP_ROOT/bin/inotifyd \
SMS_RELAY_WAKE_HELPER=$TEMP_ROOT/bin/wake \
SMS_RELAY_TELEPHONY_DIRS="$TEMP_ROOT/user $TEMP_ROOT/missing $TEMP_ROOT/user-de" \
    sh "$WATCHER" --user-id 0

[ -f "$TEMP_ROOT/arguments" ] || fail inotify-not-started
expected=$(printf '%s\n' \
    "$TEMP_ROOT/bin/wake" \
    "$TEMP_ROOT/user:cwnmDdy" \
    "$TEMP_ROOT/user/mmssms.db:wn" \
    "$TEMP_ROOT/user/mmssms.db-wal:wn" \
    "$TEMP_ROOT/user/mmssms.db-shm:wn" \
    "$TEMP_ROOT/user/sms.db:wn" \
    "$TEMP_ROOT/user/sms.db-wal:wn" \
    "$TEMP_ROOT/user/sms.db-shm:wn" \
    "$TEMP_ROOT/user-de:cwnmDdy")
actual=$(cat "$TEMP_ROOT/arguments")
[ "$actual" = "$expected" ] || fail "arguments expected=$expected actual=$actual"

rm -f "$TEMP_ROOT/arguments"
SMS_RELAY_INOTIFYD=$TEMP_ROOT/bin/missing-inotifyd \
SMS_RELAY_TELEPHONY_DIRS="$TEMP_ROOT/user" \
    sh "$WATCHER" --user-id 0
[ ! -e "$TEMP_ROOT/arguments" ] || fail unavailable-inotify-started

printf '%s\n' 'PASS root_watcher_contract modes=inotify,graceful-poll-fallback'
