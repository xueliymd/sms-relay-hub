#!/system/bin/sh

set -u
umask 077

STATE_DIR=${SMS_RELAY_STATE_DIR:-/data/adb/sms-relay}
RUN_DIR=$STATE_DIR/run
CONFIG_PATH=${SMS_RELAY_CONFIG_PATH:-$STATE_DIR/config.json}
LOCK_FILE=${SMS_RELAY_LOCK_FILE:-/data/adb/sms-relay.worker.lock}
LOCK_DIR=${SMS_RELAY_LOCK_DIR:-${LOCK_FILE}.d}
WORKER_PID_PATH=$RUN_DIR/worker.pid
SERVICE_PATH=${SMS_RELAY_SERVICE_PATH:-${0%/*}/service.sh}
DISABLED_MARKER=$STATE_DIR/disabled
WAIT_SECONDS=${SMS_RELAY_WAIT_SECONDS:-30}
RUNTIME=${SMS_RELAY_RUNTIME:-/data/adb/modules/sms-relay-root/system/bin/sms-relay-runtime}
WEB_ADDRESS=127.0.0.1:35000

fail() {
    printf '%s\n' "$1" >&2
    exit 1
}

for dependency in mkdir rm mv sleep cat tr; do
    command -v "$dependency" >/dev/null 2>&1 || fail "missing_dependency"
done

[ -x "$RUNTIME" ] || fail "runtime_unavailable"

mkdir -p "$STATE_DIR" "$RUN_DIR" >/dev/null 2>&1 || fail "state_unavailable"

write_disabled_marker() {
    marker_tmp=$STATE_DIR/.disabled.$$
    : > "$marker_tmp" || fail "state_unavailable"
    if ! mv -f "$marker_tmp" "$DISABLED_MARKER" >/dev/null 2>&1; then
        rm -f "$marker_tmp" >/dev/null 2>&1 || true
        fail "state_unavailable"
    fi
    worker_pid_value=$(cat "$RUN_DIR/worker.pid" 2>/dev/null || true)
    case "$worker_pid_value" in
        ''|*[!0-9]*) ;;
        *) kill -USR1 "$worker_pid_value" >/dev/null 2>&1 || true ;;
    esac
}

# Probe the actual worker lock. A successful non-blocking flock means no
# service process currently owns the lock; the descriptor is then released.
worker_lock_is_free() {
    if ! command -v flock >/dev/null 2>&1; then
        directory_lock_is_free
        return $?
    fi
    exec 9>"$LOCK_FILE" || return 1
    flock_error=$RUN_DIR/.flock-error.$$
    if flock -n 9 2>"$flock_error"; then
        rm -f "$flock_error" >/dev/null 2>&1 || true
        flock -u 9 >/dev/null 2>&1 || true
        exec 9>&-
        return 0
    fi
    flock_status=$?
    exec 9>&-
    flock_detail=$(cat "$flock_error" 2>/dev/null || true)
    rm -f "$flock_error" >/dev/null 2>&1 || true
    case "$flock_detail" in
        *"Bad file descriptor"*|*"Invalid argument"*)
            directory_lock_is_free
            return $?
            ;;
    esac
    if [ "$flock_status" = 127 ]; then
        directory_lock_is_free
        return $?
    fi
    return 1
}

directory_lock_is_free() {
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
        return 0
    fi
    stale_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
    case "$stale_pid" in
        ''|*[!0-9]*) stale_lock=1 ;;
        *)
            if kill -0 "$stale_pid" >/dev/null 2>&1 && worker_pid_is_service "$stale_pid"; then
                return 1
            fi
            stale_lock=1
            ;;
    esac
    [ "${stale_lock:-0}" = 1 ] || return 1
    rm -rf "$LOCK_DIR" >/dev/null 2>&1 || return 1
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
        return 0
    fi
    return 1
}

worker_pid() {
    [ -f "$WORKER_PID_PATH" ] || return 1
    worker_pid_value=$(cat "$WORKER_PID_PATH" 2>/dev/null || true)
    case "$worker_pid_value" in
        ''|*[!0-9]*) return 1 ;;
    esac
    printf '%s\n' "$worker_pid_value"
}

worker_pid_is_service() {
    worker_pid_value=$1
    if [ -r "/proc/$worker_pid_value/cmdline" ]; then
        worker_cmdline=$(tr '\000' ' ' < "/proc/$worker_pid_value/cmdline" 2>/dev/null || true)
        case "$worker_cmdline" in
            *service.sh*) return 0 ;;
            *) return 1 ;;
        esac
    fi
    # Some recovery shells do not expose /proc. The PID liveness check still
    # prevents the APatch/KernelSU action from relying on a shell FD probe.
    return 0
}

worker_is_running() {
    worker_pid_value=$(worker_pid) || return 1
    kill -0 "$worker_pid_value" >/dev/null 2>&1 || return 1
    worker_pid_is_service "$worker_pid_value"
}

wait_for_worker_exit() {
    wait_count=0
    while [ "$wait_count" -lt "$WAIT_SECONDS" ]; do
        if [ -f "$WORKER_PID_PATH" ]; then
            if ! worker_is_running; then
                rm -f "$WORKER_PID_PATH" >/dev/null 2>&1 || true
                return 0
            fi
        elif worker_lock_is_free; then
            return 0
        fi
        sleep 1
        wait_count=$((wait_count + 1))
    done
    if [ -f "$WORKER_PID_PATH" ]; then
        ! worker_is_running
    else
        worker_lock_is_free
    fi
}

start_worker() {
    if worker_is_running; then
        printf '%s\n' "enabled"
        return 0
    fi
    rm -f "$WORKER_PID_PATH" >/dev/null 2>&1 || true
    rm -f "$DISABLED_MARKER" >/dev/null 2>&1 || fail "state_unavailable"
    [ -f "$SERVICE_PATH" ] || fail "service_unavailable"
    sh "$SERVICE_PATH" >/dev/null 2>&1 &
    wait_count=0
    while [ "$wait_count" -lt 5 ]; do
        if worker_is_running; then
            printf '%s\n' "enabled"
            return 0
        fi
        sleep 1
        wait_count=$((wait_count + 1))
    done
    fail "start_pending"
}

open_web_console() {
    "$RUNTIME" web \
        --config "$CONFIG_PATH" \
        --state-dir "$STATE_DIR" \
        --listen "$WEB_ADDRESS" \
        --service "$SERVICE_PATH" >/dev/null 2>&1 &
    web_pid=$!
    token=
    wait_count=0
    while [ "$wait_count" -lt 5 ]; do
        token=$("$RUNTIME" web-token --state-dir "$STATE_DIR" 2>/dev/null || true)
        [ -n "$token" ] && break
        if ! kill -0 "$web_pid" >/dev/null 2>&1; then
            # A second click may have raced an existing console. Its token is
            # still valid if the existing process owns the listener.
            token=$("$RUNTIME" web-token --state-dir "$STATE_DIR" 2>/dev/null || true)
            [ -n "$token" ] && break
            fail "web_unavailable"
        fi
        sleep 1
        wait_count=$((wait_count + 1))
    done
    [ -n "$token" ] || fail "web_unavailable"
    command -v am >/dev/null 2>&1 || fail "missing_dependency"
    am start -a android.intent.action.VIEW -d "http://$WEB_ADDRESS/#$token" >/dev/null 2>&1 || fail "web_open_failed"
    printf '%s\n' "web_opened"
}

case "${1:-web}" in
    web|console)
        open_web_console
        ;;
    start|enable)
        # A current worker is tracked by PID. Legacy installs without a PID
        # file still use one final lock probe for upgrade compatibility.
        if [ ! -f "$WORKER_PID_PATH" ] && ! worker_lock_is_free; then
            printf '%s\n' "start_pending" >&2
            exit 1
        fi
        start_worker
        ;;
    stop|disable)
        write_disabled_marker
        if ! wait_for_worker_exit; then
            printf '%s\n' "stop_pending" >&2
            exit 1
        fi
        printf '%s\n' "disabled"
        ;;
    revoke)
        write_disabled_marker
        if ! wait_for_worker_exit; then
            printf '%s\n' "revoke_pending" >&2
            exit 1
        fi
        printf '%s\n' "credential_revocation_required"
        ;;
    rotate)
        printf '%s\n' "credential_rotation_requires_stop" >&2
        exit 1
        ;;
    status)
        if [ -e "$DISABLED_MARKER" ]; then
            printf '%s\n' "disabled"
        elif [ ! -f "$CONFIG_PATH" ]; then
            printf '%s\n' "not_configured"
        elif [ -f "$WORKER_PID_PATH" ] && worker_is_running; then
            printf '%s\n' "enabled"
        elif [ ! -f "$WORKER_PID_PATH" ] && ! worker_lock_is_free; then
            printf '%s\n' "enabled"
        elif [ -f "$WORKER_PID_PATH" ]; then
            printf '%s\n' "worker_stopped"
        else
            printf '%s\n' "worker_stopped"
        fi
        ;;
    *)
        printf '%s\n' "usage_error" >&2
        exit 1
        ;;
esac
