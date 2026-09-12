#!/system/bin/sh

# This hook runs for an explicit module uninstall. Normal module upgrades do
# not invoke it, so the durable relay state survives upgrades.
set -u
umask 077

STATE_DIR=${SMS_RELAY_STATE_DIR:-/data/adb/sms-relay}
LOCK_FILE=${SMS_RELAY_LOCK_FILE:-/data/adb/sms-relay.worker.lock}
LOCK_DIR=${SMS_RELAY_LOCK_DIR:-${LOCK_FILE}.d}
WORKER_PID_PATH=$STATE_DIR/run/worker.pid
WEB_PID_PATH=$STATE_DIR/run/web.pid

# Only the two expected production paths may be removed. The test harness may
# opt into a temporary state path without weakening the production default.
if [ "${SMS_RELAY_ALLOW_CUSTOM_STATE:-0}" != 1 ]; then
    [ "$STATE_DIR" = /data/adb/sms-relay ] || exit 0
    [ "$LOCK_FILE" = /data/adb/sms-relay.worker.lock ] || exit 0
fi

if [ -d "$STATE_DIR" ]; then
    : > "$STATE_DIR/disabled" 2>/dev/null || true
fi
worker_pid=$(cat "$WORKER_PID_PATH" 2>/dev/null || true)
worker_is_service=0
case "$worker_pid" in
    ''|*[!0-9]*) ;;
    *)
        worker_cmdline=$(tr '\000' ' ' < "/proc/$worker_pid/cmdline" 2>/dev/null || true)
        case "$worker_cmdline" in
            *service.sh*)
                worker_is_service=1
                kill -TERM "$worker_pid" >/dev/null 2>&1 || true
                wait_count=0
                while kill -0 "$worker_pid" >/dev/null 2>&1 && [ "$wait_count" -lt 5 ]; do
                    sleep 1
                    wait_count=$((wait_count + 1))
                done
                ;;
        esac
        ;;
esac
web_pid=$(cat "$WEB_PID_PATH" 2>/dev/null || true)
case "$web_pid" in
    ''|*[!0-9]*) ;;
    *)
        web_cmdline=$(tr '\000' ' ' < "/proc/$web_pid/cmdline" 2>/dev/null || true)
        case "$web_cmdline" in
            *sms-relay-runtime*web*)
                kill -TERM "$web_pid" >/dev/null 2>&1 || true
                ;;
        esac
        ;;
esac
if [ "$worker_is_service" = 1 ] && [ -n "${worker_pid:-}" ] && kill -0 "$worker_pid" >/dev/null 2>&1; then
    current_worker_cmdline=$(tr '\000' ' ' < "/proc/$worker_pid/cmdline" 2>/dev/null || true)
    case "$current_worker_cmdline" in
        *service.sh*) kill -KILL "$worker_pid" >/dev/null 2>&1 || true ;;
    esac
fi
rm -rf "$STATE_DIR" 2>/dev/null || true
rm -f "$LOCK_FILE" 2>/dev/null || true
rm -rf "$LOCK_DIR" 2>/dev/null || true
