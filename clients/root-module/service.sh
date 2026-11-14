#!/system/bin/sh

set -u
umask 077

MODDIR=${0%/*}
STATE_DIR=${SMS_RELAY_STATE_DIR:-/data/adb/sms-relay}
CONFIG_PATH=$STATE_DIR/config.json
SPOOL_DIR=$STATE_DIR/spool
RUN_DIR=$STATE_DIR/run
INFLIGHT_DIR=$RUN_DIR/inflight
WORKER_PID_PATH=$RUN_DIR/worker.pid
READER_PID_PATH=$RUN_DIR/reader.pid
WATCHER_PID_PATH=$RUN_DIR/watcher.pid
WATCHER_USER_PATH=$RUN_DIR/watcher.user
WATCHER_PATHS_PATH=$RUN_DIR/watcher.paths
STATUS_PATH=$RUN_DIR/status.json
TELEPHONY_CHANGED_MARKER=$RUN_DIR/telephony.changed
CONFIG_CHANGED_MARKER=$RUN_DIR/config.changed
QUARANTINE_DIR=$STATE_DIR/quarantine
LOCK_FILE=${SMS_RELAY_LOCK_FILE:-/data/adb/sms-relay.worker.lock}
LOCK_DIR=${SMS_RELAY_LOCK_DIR:-${LOCK_FILE}.d}
LOCK_HELD=0
DISABLED_MARKER=$STATE_DIR/disabled
UPLOADER=${SMS_RELAY_UPLOADER:-$MODDIR/system/bin/sms-relay-upload}
RUNTIME=${SMS_RELAY_RUNTIME:-$MODDIR/system/bin/sms-relay-runtime}
WATCHER=${SMS_RELAY_WATCHER:-$MODDIR/system/bin/sms-relay-watch}
BACKOFF_SECONDS=1
CLAIM_COUNTER=0
WATCHER_IDLE_SECONDS=${SMS_RELAY_WATCHER_IDLE_SECONDS:-1}
WAKE_REQUESTED=0
WAIT_PID=
WATCHER_RETRY_AT=0
MQTT_TOPIC_MAX_BYTES=512
COMMAND_POLL_INTERVAL_SECONDS=60
command_poll_fallback_elapsed=60
last_command_poll=-1
case "$WATCHER_IDLE_SECONDS" in
    ''|*[!0-9]*|0) WATCHER_IDLE_SECONDS=1 ;;
esac

command_clock_seconds() {
    command_clock_value=$(date +%s 2>/dev/null || true)
    case "$command_clock_value" in
        ''|*[!0-9]*|0) ;;
        *) printf '%s\n' "$command_clock_value"; return 0 ;;
    esac
    command_clock_uptime=$(cat /proc/uptime 2>/dev/null || true)
    command_clock_uptime=${command_clock_uptime%%.*}
    case "$command_clock_uptime" in
        ''|*[!0-9]*) return 1 ;;
        *) printf '%s\n' "$command_clock_uptime"; return 0 ;;
    esac
}

write_runtime_status() {
    status_state=$1
    status_detail=$2
    [ -d "$RUN_DIR" ] || return 0
    status_updated_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ') || return 1
    status_temporary=$RUN_DIR/.status.$$
    if ! printf '{"state":"%s","detail":"%s","updated_at":"%s"}\n' \
        "$status_state" "$status_detail" "$status_updated_at" > "$status_temporary"; then
        rm -f "$status_temporary" >/dev/null 2>&1 || true
        return 1
    fi
    if ! chmod 600 "$status_temporary" || ! mv "$status_temporary" "$STATUS_PATH"; then
        rm -f "$status_temporary" >/dev/null 2>&1 || true
        return 1
    fi
}

status_error() {
    printf '%s\n' "$1" >&2
}

cleanup() {
	# A contender that failed to acquire the worker lock does not own any of
	# the shared PID files. Leaving here prevents it from killing the active
	# worker's reader or watcher during its EXIT trap.
	if [ "$LOCK_HELD" = "0" ]; then
		WAIT_PID=
		return 0
	fi
	case "$WAIT_PID" in
		''|*[!0-9]*) ;;
		*) kill "$WAIT_PID" >/dev/null 2>&1 || true ;;
	esac
	WAIT_PID=
	if [ -f "$READER_PID_PATH" ]; then
		reader_pid_value=$(cat "$READER_PID_PATH" 2>/dev/null || true)
		case "$reader_pid_value" in
			''|*[!0-9]*) ;;
			*) kill "$reader_pid_value" >/dev/null 2>&1 || true ;;
		esac
		rm -f "$READER_PID_PATH" >/dev/null 2>&1 || true
	fi
	if [ -f "$WATCHER_PID_PATH" ]; then
		watcher_pid_value=$(cat "$WATCHER_PID_PATH" 2>/dev/null || true)
		case "$watcher_pid_value" in
			''|*[!0-9]*) ;;
			*) kill "$watcher_pid_value" >/dev/null 2>&1 || true ;;
		esac
		rm -f "$WATCHER_PID_PATH" >/dev/null 2>&1 || true
	fi
	rm -f "$WATCHER_USER_PATH" >/dev/null 2>&1 || true
	rm -f "$WATCHER_PATHS_PATH" >/dev/null 2>&1 || true
	if [ -f "$WORKER_PID_PATH" ]; then
        worker_pid_value=$(cat "$WORKER_PID_PATH" 2>/dev/null || true)
        if [ "$worker_pid_value" = "$$" ]; then
            rm -f "$WORKER_PID_PATH" >/dev/null 2>&1 || true
        fi
    fi
    if [ "$LOCK_HELD" = "1" ]; then
        flock -u 9 >/dev/null 2>&1 || true
        exec 9>&-
    elif [ "$LOCK_HELD" = "2" ]; then
        rm -f "$LOCK_DIR/pid" >/dev/null 2>&1 || true
        rmdir "$LOCK_DIR" >/dev/null 2>&1 || true
    fi
    LOCK_HELD=0
}

run_reader() {
	"$RUNTIME" read \
		--config "$CONFIG_PATH" \
		--state-dir "$STATE_DIR/reader" \
		--spool-dir "$SPOOL_DIR" \
		--user-id "$android_user_id" >/dev/null 2>&1 &
	reader_pid=$!
	reader_pid_temporary=$RUN_DIR/.reader.pid.$$
	if ! printf '%s\n' "$reader_pid" > "$reader_pid_temporary" ||
		! chmod 600 "$reader_pid_temporary" ||
		! mv -f "$reader_pid_temporary" "$READER_PID_PATH"; then
		rm -f "$reader_pid_temporary" >/dev/null 2>&1 || true
		kill "$reader_pid" >/dev/null 2>&1 || true
		return 1
	fi
	wait "$reader_pid"
	reader_status=$?
	rm -f "$READER_PID_PATH" >/dev/null 2>&1 || true
	return "$reader_status"
}

stop_watcher() {
	if [ -f "$WATCHER_PID_PATH" ]; then
		watcher_pid_value=$(cat "$WATCHER_PID_PATH" 2>/dev/null || true)
		case "$watcher_pid_value" in
			''|*[!0-9]*) ;;
			*) kill "$watcher_pid_value" >/dev/null 2>&1 || true ;;
		esac
		rm -f "$WATCHER_PID_PATH" >/dev/null 2>&1 || true
	fi
	rm -f "$WATCHER_USER_PATH" >/dev/null 2>&1 || true
	rm -f "$WATCHER_PATHS_PATH" >/dev/null 2>&1 || true
}

watcher_directory_signature() {
	for watcher_directory in \
		"/data/user/$android_user_id/com.android.providers.telephony/databases" \
		"/data/data/com.android.providers.telephony/databases" \
		"/data/user_de/$android_user_id/com.android.providers.telephony/databases"; do
		[ -d "$watcher_directory" ] || continue
		printf '%s\n' "$watcher_directory"
	done
}

start_watcher() {
	if [ "$enabled" != "true" ]; then
		stop_watcher
		return 0
	fi
	[ -x "$WATCHER" ] || return 0
	watcher_now=$(date '+%s' 2>/dev/null || printf '0')
	watcher_current_paths=$(watcher_directory_signature)
	watcher_existing_pid=$(cat "$WATCHER_PID_PATH" 2>/dev/null || true)
	watcher_existing_user=$(cat "$WATCHER_USER_PATH" 2>/dev/null || true)
	watcher_existing_paths=$(cat "$WATCHER_PATHS_PATH" 2>/dev/null || true)
	watcher_existing_alive=0
	case "$watcher_existing_pid" in
		''|*[!0-9]*) ;;
		*)
			if kill -0 "$watcher_existing_pid" >/dev/null 2>&1; then
				watcher_existing_alive=1
				if [ "$watcher_existing_user" = "$android_user_id" ] && [ "$watcher_existing_paths" = "$watcher_current_paths" ]; then
					return 0
				fi
			fi
			;;
	esac
	if [ "$watcher_existing_alive" = 0 ] && [ "$watcher_now" -lt "$WATCHER_RETRY_AT" ]; then
		return 0
	fi
	stop_watcher
	SMS_RELAY_STATE_DIR="$STATE_DIR" \
	SMS_RELAY_ANDROID_USER_ID="$android_user_id" \
	SMS_RELAY_WAKE_HELPER="$MODDIR/system/bin/sms-relay-wake" \
		"$WATCHER" >/dev/null 2>&1 &
	watcher_pid=$!
	watcher_pid_temporary=$RUN_DIR/.watcher.pid.$$ 
	if ! printf '%s\n' "$watcher_pid" > "$watcher_pid_temporary" ||
		! chmod 600 "$watcher_pid_temporary" ||
		! mv -f "$watcher_pid_temporary" "$WATCHER_PID_PATH"; then
		rm -f "$watcher_pid_temporary" >/dev/null 2>&1 || true
		kill "$watcher_pid" >/dev/null 2>&1 || true
		return 1
	fi
	watcher_user_temporary=$RUN_DIR/.watcher.user.$$
	if ! printf '%s\n' "$android_user_id" > "$watcher_user_temporary" ||
		! chmod 600 "$watcher_user_temporary" ||
		! mv -f "$watcher_user_temporary" "$WATCHER_USER_PATH"; then
		rm -f "$watcher_user_temporary" >/dev/null 2>&1 || true
		stop_watcher
		return 1
	fi
	watcher_paths_temporary=$RUN_DIR/.watcher.paths.$$
	if ! printf '%s\n' "$watcher_current_paths" > "$watcher_paths_temporary" ||
		! chmod 600 "$watcher_paths_temporary" ||
		! mv -f "$watcher_paths_temporary" "$WATCHER_PATHS_PATH"; then
		rm -f "$watcher_paths_temporary" >/dev/null 2>&1 || true
		stop_watcher
		return 1
	fi
	WATCHER_RETRY_AT=$((watcher_now + 10))
	return 0
}

handle_signal() {
    signal_status=$1
    exit "$signal_status"
}

handle_wake() {
    WAKE_REQUESTED=1
	case "$WAIT_PID" in
		''|*[!0-9]*) ;;
		*) kill "$WAIT_PID" >/dev/null 2>&1 || true ;;
	esac
}

watcher_is_running() {
    watcher_pid_value=$(cat "$WATCHER_PID_PATH" 2>/dev/null || true)
    case "$watcher_pid_value" in
        ''|*[!0-9]*) return 1 ;;
    esac
    kill -0 "$watcher_pid_value" >/dev/null 2>&1
}

acquire_lock() {
    if ! command -v flock >/dev/null 2>&1; then
        acquire_directory_lock
        return $?
    fi
    exec 9>"$LOCK_FILE" || return 2
    flock_error=$RUN_DIR/.flock-error.$$
    flock -n 9 2>"$flock_error"
    flock_status=$?
    if [ "$flock_status" = 0 ]; then
        rm -f "$flock_error" >/dev/null 2>&1 || true
        LOCK_HELD=1
        return 0
    fi
    exec 9>&-
    flock_detail=$(cat "$flock_error" 2>/dev/null || true)
    rm -f "$flock_error" >/dev/null 2>&1 || true
    case "$flock_detail" in
        *"Bad file descriptor"*|*"Invalid argument"*) acquire_directory_lock; return $? ;;
    esac
    if [ "$flock_status" = 127 ]; then
        acquire_directory_lock
        return $?
    fi
    return 1
}

pid_is_service() {
    lock_pid=$1
    if [ -r "/proc/$lock_pid/cmdline" ]; then
        lock_cmdline=$(tr '\000' ' ' < "/proc/$lock_pid/cmdline" 2>/dev/null || true)
        case "$lock_cmdline" in *service.sh*) return 0 ;; *) return 1 ;; esac
    fi
    return 0
}

acquire_directory_lock() {
    stale_lock=0
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" > "$LOCK_DIR/pid" || { rmdir "$LOCK_DIR" >/dev/null 2>&1 || true; return 2; }
        chmod 600 "$LOCK_DIR/pid" >/dev/null 2>&1 || true
        LOCK_HELD=2
        return 0
    fi
    stale_pid=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
    case "$stale_pid" in
        ''|*[!0-9]*) stale_lock=1 ;;
        *)
            if kill -0 "$stale_pid" >/dev/null 2>&1 && pid_is_service "$stale_pid"; then
                return 1
            fi
            stale_lock=1
            ;;
    esac
    [ "${stale_lock:-0}" = 1 ] || return 1
    rm -rf "$LOCK_DIR" >/dev/null 2>&1 || return 2
    if mkdir "$LOCK_DIR" 2>/dev/null; then
        printf '%s\n' "$$" > "$LOCK_DIR/pid" || return 2
        chmod 600 "$LOCK_DIR/pid" >/dev/null 2>&1 || true
        LOCK_HELD=2
        return 0
    fi
    return 1
}

write_worker_pid() {
    worker_pid_temporary=$RUN_DIR/.worker.pid.$$
    if ! printf '%s\n' "$$" > "$worker_pid_temporary"; then
        rm -f "$worker_pid_temporary" >/dev/null 2>&1 || true
        return 1
    fi
    chmod 600 "$worker_pid_temporary" >/dev/null 2>&1 || {
        rm -f "$worker_pid_temporary" >/dev/null 2>&1 || true
        return 1
    }
    if ! mv -f "$worker_pid_temporary" "$WORKER_PID_PATH" >/dev/null 2>&1; then
        rm -f "$worker_pid_temporary" >/dev/null 2>&1 || true
        return 1
    fi
    return 0
}

claim_spool_message() {
    spool_path=$1
    claim_basename=${spool_path##*/}
    claim_attempt=0
    while [ "$claim_attempt" -le 1000 ]; do
        CLAIM_COUNTER=$((CLAIM_COUNTER + 1))
        CLAIM_DIR=$INFLIGHT_DIR/claim.$$.$CLAIM_COUNTER
        if ! mkdir "$CLAIM_DIR" 2>/dev/null; then
            claim_attempt=$((claim_attempt + 1))
            continue
        fi
        if ! chmod 700 "$CLAIM_DIR"; then
            rmdir "$CLAIM_DIR" >/dev/null 2>&1 || true
            status_error "claim_failed"
            return 1
        fi
        CLAIM_PATH=$CLAIM_DIR/$claim_basename
        if mv "$spool_path" "$CLAIM_PATH" >/dev/null 2>&1; then
            return 0
        fi
        rmdir "$CLAIM_DIR" >/dev/null 2>&1 || true
        status_error "claim_failed"
        return 1
    done
    status_error "claim_failed"
    return 1
}

cleanup_claim_directory() {
    claim_path=$1
    claim_directory=${claim_path%/*}
    if ! rmdir "$claim_directory" >/dev/null 2>&1; then
        status_error "claim_cleanup_failed"
    fi
}

quarantine_message() {
    body_path=$1
    terminal_outcome=$2
    case "$terminal_outcome" in
        409|422) quarantine_reason=http_${terminal_outcome}_terminal ;;
        local_body_invalid|local_body_too_large|local_body_read_failed) quarantine_reason=$terminal_outcome ;;
        *) return 1 ;;
    esac
    quarantine_directory=$QUARANTINE_DIR/$quarantine_reason
    quarantine_basename=${body_path##*/}
    quarantine_index=0
    while [ "$quarantine_index" -le 1000 ]; do
        if [ "$quarantine_index" = 0 ]; then
            quarantine_target=$quarantine_directory/$quarantine_basename
        else
            quarantine_target=$quarantine_directory/$quarantine_basename.$quarantine_index
        fi
        if [ -e "$quarantine_target" ]; then
            quarantine_index=$((quarantine_index + 1))
            continue
        fi
        if ln "$body_path" "$quarantine_target" 2>/dev/null; then
            if rm "$body_path" >/dev/null 2>&1; then
                return 0
            fi
            rm -f "$quarantine_target" >/dev/null 2>&1 || true
            status_error "quarantine_failed"
            return 1
        fi
        if [ -e "$quarantine_target" ]; then
            quarantine_index=$((quarantine_index + 1))
            continue
        fi
        status_error "quarantine_failed"
        return 1
    done
    status_error "quarantine_failed"
    return 1
}

process_claim_message() {
    claim_path=$1
    message_id=${claim_path##*/}
    message_id=${message_id%.json}
    [ ! -e "$DISABLED_MARKER" ] || return 2
    upload_status=$("$UPLOADER" --config "$CONFIG_PATH" --idempotency-key "$message_id" "$claim_path" 2>/dev/null)
    case "$upload_status" in
        2??)
            if ! rm "$claim_path" >/dev/null 2>&1; then
                status_error "claim_delete_failed"
                return 1
            fi
            cleanup_claim_directory "$claim_path"
            write_runtime_status "uploaded" "$upload_status" || true
            return 0
            ;;
        409|422|local_body_invalid|local_body_too_large|local_body_read_failed)
            if ! quarantine_message "$claim_path" "$upload_status"; then
                return 1
            fi
            cleanup_claim_directory "$claim_path"
            write_runtime_status "quarantined" "$upload_status" || true
            return 0
            ;;
        *)
            write_runtime_status "upload_failed" "$upload_status" || true
            return 1
            ;;
    esac
}

process_inflight_claims() {
    for claim_path in "$INFLIGHT_DIR"/*/*.json; do
        [ -f "$claim_path" ] || continue
        process_claim_message "$claim_path"
        claim_status=$?
        case "$claim_status" in
            0) ;;
            2) return 2 ;;
            *) return 1 ;;
        esac
    done
    return 0
}

trim_line() {
    trim_value=$1
    trim_cr=$(printf '\r')
    trim_tab=$(printf '\t')
    case "$trim_value" in *"$trim_cr") trim_value=${trim_value%"$trim_cr"} ;; esac
    while :; do
        case "$trim_value" in
            ' '*) trim_value=${trim_value# } ;;
            "$trim_tab"*) trim_value=${trim_value#"$trim_tab"} ;;
            *) break ;;
        esac
    done
    while :; do
        case "$trim_value" in
            *' ') trim_value=${trim_value% } ;;
            *"$trim_tab") trim_value=${trim_value%"$trim_tab"} ;;
            *) break ;;
        esac
    done
    printf '%s' "$trim_value"
}

parse_scalar_token() {
    key=; value=; value_type=
    case "$token" in
        \"*) ;;
        *) return 1 ;;
    esac
    token_after_open=${token#\"}
    key=${token_after_open%%\"*}
    [ -n "$key" ] || return 1
    token_after_key=${token_after_open#*\"}
    token_after_key=$(trim_line "$token_after_key")
    case "$token_after_key" in
        :*) token_value=${token_after_key#:} ;;
        *) return 1 ;;
    esac
    token_value=$(trim_line "$token_value")

    first_key_character=${key%"${key#?}"}
    remaining_key_characters=${key#?}
    case "$first_key_character" in
        [A-Za-z_]) ;;
        *) return 1 ;;
    esac
    case "$remaining_key_characters" in
        *[!A-Za-z0-9_]*) return 1 ;;
    esac

    case "$token_value" in
        \"*\")
            value=${token_value#\"}
            value=${value%\"}
            case "$value" in
                *'"'*|*\\*) return 1 ;;
            esac
            value_type=string
            ;;
        true|false)
            value=$token_value
            value_type=boolean
            ;;
        0|[1-9]|[1-9][0-9]*)
            case "$token_value" in *[!0-9]*) return 1 ;; esac
            value=$token_value
            value_type=integer
            ;;
        *) return 1 ;;
    esac
    return 0
}

# Strict flat config parser: one top-level key per line, exact scalar tokens,
# unique known keys, and no nested objects, arrays, escapes, or extra fields.
parse_config() {
    config_open=0; config_closed=0; config_fields=0; config_previous_comma=0
    seen_schema_version=0; seen_enabled=0; seen_base_url=0; seen_allow_insecure_local_http=0; seen_transport_mode=0; seen_fallback_to_http=0; seen_device_id=0
    seen_hmac_secret=0; seen_source=0; seen_android_user_id=0; seen_initial_sync=0
    seen_poll_interval=0; seen_max_backoff=0; seen_watcher_idle=0; seen_sms_database_path=0; seen_allow_query_battery=0; seen_allow_query_recent_sms=0; seen_allow_trigger_upload=0
    seen_mqtt_broker_url=0; seen_mqtt_topic=0; seen_mqtt_username=0; seen_mqtt_password=0
    config_schema_version=; enabled=; config_base_url=; allow_insecure_local_http=false; transport_mode=http; fallback_to_http=true; config_device_id=
    config_hmac_secret=; configured_source=; android_user_id=; initial_sync_mode=
    poll_interval=; max_backoff=; watcher_idle=1; sms_database_path=; mqtt_broker_url=; mqtt_topic=; mqtt_username=; mqtt_password=; allow_query_battery=false; allow_query_recent_sms=false; allow_trigger_upload=false

    raw_line=
    while IFS= read -r raw_line || [ -n "$raw_line" ]; do
        line=$(trim_line "$raw_line")
        [ -n "$line" ] || return 1
        if [ "$config_open" = 0 ]; then
            [ "$line" = "{" ] || return 1
            config_open=1; continue
        fi
        [ "$config_closed" = 0 ] || return 1
        if [ "$line" = "}" ]; then
            [ "$config_fields" -gt 0 ] && [ "$config_previous_comma" = 0 ] || return 1
            config_closed=1; continue
        fi
        if [ "$config_fields" -gt 0 ] && [ "$config_previous_comma" != 1 ]; then
            return 1
        fi
        has_comma=0; token=$line
        case "$token" in *,) has_comma=1; token=${token%,} ;; esac
        parse_scalar_token || return 1
        case "$key" in
            schema_version) [ "$value_type" = integer ] && [ "$seen_schema_version" = 0 ] || return 1; seen_schema_version=1; config_schema_version=$value ;;
            enabled) [ "$value_type" = boolean ] && [ "$seen_enabled" = 0 ] || return 1; seen_enabled=1; enabled=$value ;;
            base_url) [ "$value_type" = string ] && [ "$seen_base_url" = 0 ] || return 1; seen_base_url=1; config_base_url=$value ;;
            allow_insecure_local_http) [ "$value_type" = boolean ] && [ "$seen_allow_insecure_local_http" = 0 ] || return 1; seen_allow_insecure_local_http=1; allow_insecure_local_http=$value ;;
            transport_mode) [ "$value_type" = string ] && [ "$seen_transport_mode" = 0 ] || return 1; seen_transport_mode=1; transport_mode=$value ;;
            fallback_to_http) [ "$value_type" = boolean ] && [ "$seen_fallback_to_http" = 0 ] || return 1; seen_fallback_to_http=1; fallback_to_http=$value ;;
            device_id) [ "$value_type" = string ] && [ "$seen_device_id" = 0 ] || return 1; seen_device_id=1; config_device_id=$value ;;
            hmac_secret) [ "$value_type" = string ] && [ "$seen_hmac_secret" = 0 ] || return 1; seen_hmac_secret=1; config_hmac_secret=$value ;;
            source) [ "$value_type" = string ] && [ "$seen_source" = 0 ] || return 1; seen_source=1; configured_source=$value ;;
            android_user_id) [ "$value_type" = integer ] && [ "$seen_android_user_id" = 0 ] || return 1; seen_android_user_id=1; android_user_id=$value ;;
            initial_sync_mode) [ "$value_type" = string ] && [ "$seen_initial_sync" = 0 ] || return 1; seen_initial_sync=1; initial_sync_mode=$value ;;
            poll_interval_seconds) [ "$value_type" = integer ] && [ "$seen_poll_interval" = 0 ] || return 1; seen_poll_interval=1; poll_interval=$value ;;
            max_backoff_seconds) [ "$value_type" = integer ] && [ "$seen_max_backoff" = 0 ] || return 1; seen_max_backoff=1; max_backoff=$value ;;
            watcher_idle_seconds) [ "$value_type" = integer ] && [ "$seen_watcher_idle" = 0 ] || return 1; seen_watcher_idle=1; watcher_idle=$value ;;
            sms_database_path) [ "$value_type" = string ] && [ "$seen_sms_database_path" = 0 ] || return 1; seen_sms_database_path=1; sms_database_path=$value ;;
            allow_query_battery) [ "$value_type" = boolean ] && [ "$seen_allow_query_battery" = 0 ] || return 1; seen_allow_query_battery=1; allow_query_battery=$value ;;
            allow_query_recent_sms) [ "$value_type" = boolean ] && [ "$seen_allow_query_recent_sms" = 0 ] || return 1; seen_allow_query_recent_sms=1; allow_query_recent_sms=$value ;;
            allow_trigger_upload) [ "$value_type" = boolean ] && [ "$seen_allow_trigger_upload" = 0 ] || return 1; seen_allow_trigger_upload=1; allow_trigger_upload=$value ;;
            mqtt_broker_url) [ "$value_type" = string ] && [ "$seen_mqtt_broker_url" = 0 ] || return 1; seen_mqtt_broker_url=1; mqtt_broker_url=$value ;;
            mqtt_topic) [ "$value_type" = string ] && [ "$seen_mqtt_topic" = 0 ] || return 1; seen_mqtt_topic=1; mqtt_topic=$value ;;
            mqtt_username) [ "$value_type" = string ] && [ "$seen_mqtt_username" = 0 ] || return 1; seen_mqtt_username=1; mqtt_username=$value ;;
            mqtt_password) [ "$value_type" = string ] && [ "$seen_mqtt_password" = 0 ] || return 1; seen_mqtt_password=1; mqtt_password=$value ;;
            *) return 1 ;;
        esac
        config_fields=$((config_fields + 1)); config_previous_comma=$has_comma
    done < "$CONFIG_PATH"
    [ "$config_open" = 1 ] && [ "$config_closed" = 1 ] || return 1
    [ "$seen_schema_version" = 1 ] && [ "$seen_enabled" = 1 ] && [ "$seen_base_url" = 1 ] || return 1
    [ "$seen_device_id" = 1 ] && [ "$seen_hmac_secret" = 1 ] && [ "$seen_source" = 1 ] || return 1
    [ "$seen_android_user_id" = 1 ] && [ "$seen_initial_sync" = 1 ] || return 1
    [ "$seen_poll_interval" = 1 ] && [ "$seen_max_backoff" = 1 ] || return 1
    [ "$watcher_idle" -ge 1 ] && [ "$watcher_idle" -le 3600 ] || return 1
    [ "$config_schema_version" = 2 ] || return 1
    case "$transport_mode" in
        http|ws) ;;
        mqtt)
            [ -n "$mqtt_broker_url" ] || return 1
            case "$mqtt_broker_url" in
                mqtt://*|mqtts://*)
                    printf '%s' "$mqtt_broker_url" | grep -Eq '^(mqtt|mqtts)://[^@/[:space:]?#]+$|^(mqtt|mqtts)://[^@/[:space:]?#]+/$' || return 1
                    ;;
                ws://*|wss://*)
                    printf '%s' "$mqtt_broker_url" | grep -Eq '^(ws|wss)://[^@/[:space:]?#]+$|^(ws|wss)://[^@/[:space:]?#]+/[^[:space:]?#]*$' || return 1
                    ;;
                *) return 1 ;;
            esac
            if [ -n "$mqtt_topic" ]; then
                printf '%s' "$mqtt_topic" | grep -Eq '^[^+#[:space:]]+$' || return 1
                [ "$mqtt_topic" = 'relay/v1/devices/{device_id}/messages' ] || [ "$mqtt_topic" = "relay/v1/devices/$config_device_id/messages" ] || return 1
                mqtt_topic_bytes=$(printf '%s' "$mqtt_topic" | wc -c | tr -d '[:space:]')
                printf '%s' "$mqtt_topic_bytes" | grep -Eq '^[0-9]+$' || return 1
                [ "$mqtt_topic_bytes" -le "$MQTT_TOPIC_MAX_BYTES" ] || return 1
            fi
            ;;
        *) return 1 ;;
    esac
    return 0
}

load_config() {
    parse_config || return 1
    [ "$configured_source" = "root_module" ] || return 1
    [ "$android_user_id" -ge 0 ] || return 1
    [ "$initial_sync_mode" = "new_only" ] || return 1
    [ "$poll_interval" -ge 1 ] && [ "$poll_interval" -le 3600 ] || return 1
    [ "$max_backoff" -ge 1 ] && [ "$max_backoff" -le 3600 ] || return 1
    [ "$watcher_idle" -ge 1 ] && [ "$watcher_idle" -le 3600 ] || return 1
    WATCHER_IDLE_SECONDS=$watcher_idle
    if [ "$enabled" = "true" ]; then
        "$UPLOADER" --config "$CONFIG_PATH" --check-config >/dev/null 2>&1 || return 1
    else
        [ "$enabled" = "false" ] || return 1
    fi
}

consume_control_markers() {
	if [ -e "$CONFIG_CHANGED_MARKER" ]; then
        rm -f "$CONFIG_CHANGED_MARKER" >/dev/null 2>&1 || return 1
        load_config || return 1
		BACKOFF_SECONDS=1
		start_watcher || return 1
        write_runtime_status "config_updated" "" || true
    fi
    if [ -e "$TELEPHONY_CHANGED_MARKER" ]; then
        rm -f "$TELEPHONY_CHANGED_MARKER" >/dev/null 2>&1 || return 1
    fi
}

wait_for_poll_or_wake() {
    wait_limit=$1
	wait_kind=${2:-poll}
	start_watcher || true
	if [ "$wait_kind" = "poll" ] && watcher_is_running; then
        wait_limit=$WATCHER_IDLE_SECONDS
    fi
    WAKE_REQUESTED=0
    if [ -e "$CONFIG_CHANGED_MARKER" ] || [ -e "$TELEPHONY_CHANGED_MARKER" ] || [ -e "$DISABLED_MARKER" ]; then
        return 0
    fi
    sleep "$wait_limit" &
    WAIT_PID=$!
    if [ "$WAKE_REQUESTED" = 1 ]; then
        kill "$WAIT_PID" >/dev/null 2>&1 || true
    fi
    wait "$WAIT_PID" >/dev/null 2>&1 || true
    WAIT_PID=
    WAKE_REQUESTED=0
}

# Hold the worker lock before any marker/config/dependency work becomes visible.
# This makes action.sh lock probes authoritative for a starting service.
trap cleanup EXIT
trap 'handle_signal 129' HUP
trap 'handle_signal 130' INT
trap 'handle_signal 143' TERM
trap 'handle_wake' USR1
mkdir -p "$RUN_DIR" >/dev/null 2>&1 || {
    status_error "state_unavailable"
    exit 1
}
chmod 700 "$STATE_DIR" "$RUN_DIR" >/dev/null 2>&1 || {
    status_error "state_unavailable"
    exit 1
}
acquire_lock
lock_status=$?
case "$lock_status" in
    0) ;;
    2) status_error "state_unavailable"; exit 1 ;;
    3) status_error "missing_dependency"; exit 1 ;;
    *) status_error "already_running"; exit 1 ;;
esac

# The PID marker is written before configuration/dependency checks so action.sh
# can observe a starting worker. Ensure its parent exists on first boot too.
mkdir -p "$RUN_DIR" >/dev/null 2>&1 || {
    status_error "state_unavailable"
    exit 1
}

write_worker_pid || {
    status_error "state_unavailable"
    exit 1
}

[ ! -e "$DISABLED_MARKER" ] || exit 0

for dependency in curl awk grep sed tr wc mktemp od dd date mkdir rmdir sleep rm mv chmod ln cat; do
    command -v "$dependency" >/dev/null 2>&1 || {
        status_error "missing_dependency"
        exit 1
    }
done

mkdir -p "$SPOOL_DIR" "$RUN_DIR" "$INFLIGHT_DIR" \
    "$QUARANTINE_DIR/http_409_terminal" \
    "$QUARANTINE_DIR/http_422_terminal" \
    "$QUARANTINE_DIR/local_body_invalid" \
    "$QUARANTINE_DIR/local_body_too_large" \
    "$QUARANTINE_DIR/local_body_read_failed" >/dev/null 2>&1 || {
    status_error "state_unavailable"
    exit 1
}
chmod 700 "$SPOOL_DIR" "$RUN_DIR" "$INFLIGHT_DIR" "$QUARANTINE_DIR" \
    "$QUARANTINE_DIR/http_409_terminal" \
    "$QUARANTINE_DIR/http_422_terminal" \
    "$QUARANTINE_DIR/local_body_invalid" \
    "$QUARANTINE_DIR/local_body_too_large" \
    "$QUARANTINE_DIR/local_body_read_failed" >/dev/null 2>&1 || {
    status_error "state_unavailable"
    exit 1
}

[ -x "$UPLOADER" ] || {
    status_error "uploader_unavailable"
    exit 1
}
[ -x "$RUNTIME" ] || {
    status_error "runtime_unavailable"
    exit 1
}
if [ ! -f "$CONFIG_PATH" ]; then
    write_runtime_status "not_configured" "config_missing" || true
    while [ ! -f "$CONFIG_PATH" ]; do
        [ ! -e "$DISABLED_MARKER" ] || exit 0
        sleep 1
    done
fi

load_config || {
    write_runtime_status "config_invalid" "config_invalid" || true
    status_error "config_invalid"
    exit 1
}

if [ "$enabled" = "true" ]; then
    write_runtime_status "running" "" || true
else
    write_runtime_status "disabled" "" || true
fi

start_watcher || {
	status_error "watcher_unavailable"
	exit 1
}

while [ ! -e "$DISABLED_MARKER" ]; do
    if ! consume_control_markers; then
        status_error "config_invalid"
        exit 1
    fi
    [ -e "$DISABLED_MARKER" ] && exit 0
    if [ "$enabled" != "true" ]; then
        wait_for_poll_or_wake "$poll_interval"
        continue
    fi

    # 远程控制指令与上报共用常驻 worker，但独立按 60 秒轮询，
    # 避免空闲设备必须等到下一次短信上报才领取管理端指令。
    command_now=$(command_clock_seconds 2>/dev/null || true)
    command_poll_due=0
    case "$command_now" in
        ''|*[!0-9]*)
            command_poll_fallback_elapsed=$((command_poll_fallback_elapsed + poll_interval))
            if [ "$command_poll_fallback_elapsed" -ge "$COMMAND_POLL_INTERVAL_SECONDS" ]; then
                command_poll_due=1
            fi
            ;;
        *)
            if [ "$last_command_poll" -lt 0 ] ||
                [ "$command_now" -lt "$last_command_poll" ] ||
                [ $((command_now - last_command_poll)) -ge "$COMMAND_POLL_INTERVAL_SECONDS" ]; then
                command_poll_due=1
            fi
            ;;
    esac
    if [ "$command_poll_due" = 1 ]; then
        SMS_RELAY_WAKE_HELPER="$MODDIR/system/bin/sms-relay-wake" "$RUNTIME" commands --config "$CONFIG_PATH" >/dev/null 2>&1 || true
        case "$command_now" in
            ''|*[!0-9]*) last_command_poll=-1 ;;
            *) last_command_poll=$command_now ;;
        esac
        command_poll_fallback_elapsed=0
    fi

    upload_failed=0
    process_inflight_claims
    inflight_status=$?
    case "$inflight_status" in
        0) ;;
        2) exit 0 ;;
        *) upload_failed=1 ;;
    esac
    [ -e "$DISABLED_MARKER" ] && exit 0
    if [ "$upload_failed" = 1 ]; then
        status_error "upload_failed"
        wait_for_poll_or_wake "$BACKOFF_SECONDS" backoff
        BACKOFF_SECONDS=$((BACKOFF_SECONDS * 2))
        [ "$BACKOFF_SECONDS" -le "$max_backoff" ] || BACKOFF_SECONDS=$max_backoff
        continue
    fi

    reader_failed=0
	run_reader || reader_failed=1
	if [ "$reader_failed" = "0" ]; then
		write_runtime_status "running" "" || true
	fi
    [ -e "$DISABLED_MARKER" ] && exit 0
    if [ "$reader_failed" = "1" ]; then
        write_runtime_status "reader_failed" "reader_failed" || true
        status_error "reader_failed"
        wait_for_poll_or_wake "$poll_interval"
        continue
    fi

    for spool_path in "$SPOOL_DIR"/*.json; do
        [ -e "$DISABLED_MARKER" ] && exit 0
        [ -f "$spool_path" ] || continue
        if ! claim_spool_message "$spool_path"; then
            upload_failed=1
            break
        fi
        process_claim_message "$CLAIM_PATH"
        claim_status=$?
        case "$claim_status" in
            0) ;;
            2) exit 0 ;;
            *) upload_failed=1; break ;;
        esac
    done

    if [ "$upload_failed" = "1" ]; then
        status_error "upload_failed"
        wait_for_poll_or_wake "$BACKOFF_SECONDS" backoff
        BACKOFF_SECONDS=$((BACKOFF_SECONDS * 2))
        [ "$BACKOFF_SECONDS" -le "$max_backoff" ] || BACKOFF_SECONDS=$max_backoff
    else
        BACKOFF_SECONDS=1
        wait_for_poll_or_wake "$poll_interval"
    fi
done
