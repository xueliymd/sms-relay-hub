#!/bin/sh

set -eu
umask 077

HARNESS_ONLY=${HARNESS_ONLY:-all}
case "$HARNESS_ONLY" in
    all|uploader-contracts|response-discard|service-marker|service-dependency|action) ;;
    *)
        printf '%s\n' 'FAIL behavior=unknown-harness-case' >&2
        exit 1
        ;;
esac

TEST_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
REPOSITORY_ROOT=$(CDPATH= cd "$TEST_DIR/../../.." && pwd)
SERVICE=$REPOSITORY_ROOT/clients/root-module/service.sh
ACTION=$REPOSITORY_ROOT/clients/root-module/action.sh
UPLOADER=$REPOSITORY_ROOT/clients/root-module/system/bin/sms-relay-upload
HARNESS_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/sms-relay-root-behavior.XXXXXX")
STUB_BIN=$HARNESS_ROOT/bin
mkdir -p "$STUB_BIN"

cleanup() {
    cleanup_status=$?
    trap - EXIT HUP INT TERM
    if [ "${HARNESS_KEEP_TMP:-0}" = 1 ]; then
        printf '%s\n' "DEBUG harness_root=$HARNESS_ROOT" >&2
    else
        rm -rf "$HARNESS_ROOT"
    fi
    exit "$cleanup_status"
}
trap cleanup EXIT HUP INT TERM

fail_test() {
    printf '%s\n' "FAIL behavior=$1" >&2
    exit 1
}

assert_equal() {
    label=$1
    actual=$2
    expected=$3
    [ "$actual" = "$expected" ] || fail_test "$label expected=$expected actual=$actual"
}

assert_file() {
    [ -f "$1" ] || fail_test "$2"
}

assert_absent() {
    [ ! -e "$1" ] || fail_test "$2"
}

selected_case() {
    [ "${HARNESS_ONLY:-all}" = all ] || [ "${HARNESS_ONLY:-all}" = "$1" ]
}

REAL_MV=$(command -v mv)
REAL_RM=$(command -v rm)
REAL_LN=$(command -v ln)
REAL_SED=$(command -v sed)
REAL_SH=$(command -v sh)
REAL_SLEEP=$(command -v sleep)
export REAL_MV REAL_RM REAL_LN REAL_SED REAL_SH REAL_SLEEP

cat > "$STUB_BIN/runtime" <<'EOF'
#!/bin/sh
printf '%s\n' "${1:-}" >> "$SMS_RELAY_STATE_DIR/runtime.commands"
case "${1:-}" in
    --self-test) printf '%s\n' ready ;;
    check-config) printf '%s\n' ready ;;
    nonce) printf '%s\n' AAECAwQFBgcICQoLDA0ODxAREhMUFRYX ;;
    sha256) printf '%s\n' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
    hmac) printf '%s\n' AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8 ;;
    transport-upload)
        [ -n "${HARNESS_TRANSPORT_STATUS:-}" ] || exit 1
        printf '%s\n' "$HARNESS_TRANSPORT_STATUS"
        ;;
    read)
        reader_user_id=
        reader_config_path=
        expect_user_id=0
        expect_config_path=0
        for reader_argument in "$@"; do
            if [ "$expect_user_id" = 1 ]; then
                reader_user_id=$reader_argument
                expect_user_id=0
                continue
            fi
            if [ "$expect_config_path" = 1 ]; then
                reader_config_path=$reader_argument
                expect_config_path=0
                continue
            fi
            [ "$reader_argument" != --user-id ] || expect_user_id=1
            [ "$reader_argument" != --config ] || expect_config_path=1
        done
        if [ "${HARNESS_REQUIRE_READER_CONFIG:-0}" = 1 ] && [ ! -f "$reader_config_path" ]; then
            exit 1
        fi
        printf '%s\n' "$reader_user_id" >> "$SMS_RELAY_STATE_DIR/reader.user-ids"
        counter_file=$SMS_RELAY_STATE_DIR/reader.count
        count=0
        [ ! -f "$counter_file" ] || count=$(cat "$counter_file")
        count=$((count + 1))
        printf '%s\n' "$count" > "$counter_file"
        if [ "${HARNESS_READER_FAIL_FIRST:-0}" = 1 ] && [ "$count" -le 2 ]; then
            exit 1
        fi
        if [ -n "${HARNESS_READER_DISABLE_AFTER:-}" ] && [ "$count" -ge "$HARNESS_READER_DISABLE_AFTER" ]; then
            : > "$SMS_RELAY_STATE_DIR/disabled"
        fi
        if [ "${HARNESS_READER_BLOCK:-0}" = 1 ]; then
            if [ "${HARNESS_STOP_READER_ON_START:-0}" = 1 ]; then
                stop_wait=0
                while [ ! -f "$SMS_RELAY_STATE_DIR/run/reader.pid" ] && [ "$stop_wait" -lt 20 ]; do
                    "$REAL_SLEEP" 0.1
                    stop_wait=$((stop_wait + 1))
                done
                service_pid=$(cat "$SMS_RELAY_STATE_DIR/run/worker.pid" 2>/dev/null || true)
                [ -z "$service_pid" ] || kill -TERM "$service_pid" >/dev/null 2>&1 || true
            fi
            "$REAL_SLEEP" 30
        fi
        ;;
    web)
        mkdir -p "$SMS_RELAY_STATE_DIR/run"
        printf '%s\n' action-session-token > "$SMS_RELAY_STATE_DIR/run/web.token"
        chmod 600 "$SMS_RELAY_STATE_DIR/run/web.token"
        ;;
    web-token)
        cat "$SMS_RELAY_STATE_DIR/run/web.token"
        ;;
    *) exit 1 ;;
esac
EOF

cat > "$STUB_BIN/am" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" > "$SMS_RELAY_STATE_DIR/am.args"
EOF

cat > "$STUB_BIN/curl" <<'EOF'
#!/bin/sh
case " $* " in
    *" --help all "*)
        printf '%s\n' '--silent --show-error --output --write-out --connect-timeout --max-time --request --header --data-binary'
        exit 0
        ;;
esac
body_argument=
output_path=
expect_output_path=0
for argument in "$@"; do
    if [ "$expect_output_path" = 1 ]; then
        output_path=$argument
        expect_output_path=0
        continue
    fi
    case "$argument" in
        -o|--output) expect_output_path=1 ;;
        @*) body_argument=${argument#@} ;;
    esac
done
if [ "${HARNESS_REQUIRE_SNAPSHOT:-0}" = 1 ]; then
    case "$body_argument" in "$SMS_RELAY_TMP_PARENT"/sms-relay-upload.*/body.json) ;; *) exit 1 ;; esac
fi
if [ "${HARNESS_REQUIRE_RESPONSE_DISCARD:-0}" = 1 ]; then
    printf '%s' "$output_path" > "$SMS_RELAY_STATE_DIR/curl.output-target"
    [ "$output_path" = /dev/null ] || exit 1
fi
[ -n "${HARNESS_CURL_QUEUE:-}" ] || exit 1
status=$($REAL_SED -n '1p' "$HARNESS_CURL_QUEUE")
$REAL_SED '1d' "$HARNESS_CURL_QUEUE" > "$HARNESS_CURL_QUEUE.next"
$REAL_MV "$HARNESS_CURL_QUEUE.next" "$HARNESS_CURL_QUEUE"
if [ -n "$output_path" ] && [ -n "${HARNESS_RESPONSE_BODY:-}" ]; then
    printf '%s' "$HARNESS_RESPONSE_BODY" > "$output_path"
fi
if [ -n "${HARNESS_EXPECT_ENDPOINT:-}" ]; then
    last_argument=
    for argument in "$@"; do last_argument=$argument; done
    printf '%s' "$last_argument" > "$SMS_RELAY_STATE_DIR/curl.endpoint"
    [ "$last_argument" = "$HARNESS_EXPECT_ENDPOINT" ] || exit 1
fi
curl_count_file=$SMS_RELAY_STATE_DIR/curl.count
curl_count=0
[ ! -f "$curl_count_file" ] || curl_count=$(cat "$curl_count_file")
curl_count=$((curl_count + 1))
printf '%s\n' "$curl_count" > "$curl_count_file"
if [ -n "${HARNESS_DISABLE_AFTER_CURL_COUNT:-}" ] && [ "$curl_count" -ge "$HARNESS_DISABLE_AFTER_CURL_COUNT" ]; then
    : > "$SMS_RELAY_STATE_DIR/disabled"
fi
if [ "${HARNESS_STOP_WHEN_QUEUE_EMPTY:-0}" = 1 ] && [ ! -s "$HARNESS_CURL_QUEUE" ]; then
    : > "$SMS_RELAY_STATE_DIR/disabled"
fi
printf '%s' "$status"
EOF

cat > "$STUB_BIN/flock" <<'EOF'
#!/bin/sh
if [ "${HARNESS_FLOCK_BAD_FD:-0}" = 1 ]; then
    printf '%s\n' 'flock: Bad file descriptor' >&2
    exit 1
fi
case "${1:-}" in
    -u)
        [ "${2:-}" = 9 ] || exit 2
        exit 0
        ;;
    -n)
        [ "${2:-}" = 9 ] || exit 2
        if [ -n "${HARNESS_FLOCK_QUEUE:-}" ] && [ -s "$HARNESS_FLOCK_QUEUE" ]; then
            flock_status=$($REAL_SED -n '1p' "$HARNESS_FLOCK_QUEUE")
            $REAL_SED '1d' "$HARNESS_FLOCK_QUEUE" > "$HARNESS_FLOCK_QUEUE.next"
            $REAL_MV "$HARNESS_FLOCK_QUEUE.next" "$HARNESS_FLOCK_QUEUE"
            exit "$flock_status"
        fi
        exit "${HARNESS_FLOCK_DEFAULT:-0}"
        ;;
    *) exit 2 ;;
esac
EOF

cat > "$STUB_BIN/sleep" <<'EOF'
#!/bin/sh
printf '%s\n' "${1:-}" >> "$SMS_RELAY_STATE_DIR/sleep.arguments"
wake_worker() {
    worker_pid=$(cat "$SMS_RELAY_STATE_DIR/run/worker.pid" 2>/dev/null || true)
    [ -z "$worker_pid" ] || kill -USR1 "$worker_pid" >/dev/null 2>&1 || true
}
if [ "${HARNESS_MUTATE_CONFIG_ON_SLEEP:-0}" = 1 ]; then
    mutation_count_file=$SMS_RELAY_STATE_DIR/sleep.mutation-count
    mutation_count=0
    [ ! -f "$mutation_count_file" ] || mutation_count=$(cat "$mutation_count_file")
    mutation_count=$((mutation_count + 1))
    printf '%s\n' "$mutation_count" > "$mutation_count_file"
    if [ "$mutation_count" = 1 ]; then
        "$REAL_SED" -i 's/"android_user_id": 0/"android_user_id": 1/' "$SMS_RELAY_STATE_DIR/config.json"
        mkdir -p "$SMS_RELAY_STATE_DIR/run"
        : > "$SMS_RELAY_STATE_DIR/run/config.changed"
        wake_worker
    fi
fi
if [ "${HARNESS_CREATE_CONFIG_ON_SLEEP:-0}" = 1 ]; then
    creation_count_file=$SMS_RELAY_STATE_DIR/sleep.creation-count
    creation_count=0
    [ ! -f "$creation_count_file" ] || creation_count=$(cat "$creation_count_file")
    creation_count=$((creation_count + 1))
    printf '%s\n' "$creation_count" > "$creation_count_file"
    if [ "$creation_count" = 1 ]; then
        if grep -F '"state":"not_configured"' "$SMS_RELAY_STATE_DIR/run/status.json" >/dev/null 2>&1; then
            : > "$SMS_RELAY_STATE_DIR/not-configured-observed"
        fi
        "$REAL_MV" "$SMS_RELAY_STATE_DIR/config.pending" "$SMS_RELAY_STATE_DIR/config.json"
        mkdir -p "$SMS_RELAY_STATE_DIR/run"
        : > "$SMS_RELAY_STATE_DIR/run/config.changed"
        wake_worker
    fi
fi
if [ "${HARNESS_DISABLE_CONFIG_ON_SLEEP:-0}" = 1 ]; then
    disable_count_file=$SMS_RELAY_STATE_DIR/sleep.disable-count
    disable_count=0
    [ ! -f "$disable_count_file" ] || disable_count=$(cat "$disable_count_file")
    disable_count=$((disable_count + 1))
    printf '%s\n' "$disable_count" > "$disable_count_file"
    if [ "$disable_count" = 1 ]; then
        "$REAL_SED" -i 's/"enabled": true/"enabled": false/' "$SMS_RELAY_STATE_DIR/config.json"
        mkdir -p "$SMS_RELAY_STATE_DIR/run"
        : > "$SMS_RELAY_STATE_DIR/run/config.changed"
        wake_worker
    elif [ "$disable_count" = 2 ]; then
        [ -f "$SMS_RELAY_STATE_DIR/run/watcher.pid" ] && : > "$SMS_RELAY_STATE_DIR/watcher-disabled-observed"
        : > "$SMS_RELAY_STATE_DIR/disabled"
        wake_worker
    fi
fi
exit 0
EOF

cat > "$STUB_BIN/reader" <<'EOF'
#!/bin/sh
counter_file=$SMS_RELAY_STATE_DIR/reader.count
count=0
[ ! -f "$counter_file" ] || count=$(cat "$counter_file")
count=$((count + 1))
printf '%s\n' "$count" > "$counter_file"
if [ -n "${HARNESS_READER_DISABLE_AFTER:-}" ] && [ "$count" -ge "$HARNESS_READER_DISABLE_AFTER" ]; then
    : > "$SMS_RELAY_STATE_DIR/disabled"
fi
exit 0
EOF

cat > "$STUB_BIN/watcher" <<'EOF'
#!/bin/sh
counter_file=$SMS_RELAY_STATE_DIR/watcher.count
count=0
[ ! -f "$counter_file" ] || count=$(cat "$counter_file")
count=$((count + 1))
printf '%s\n' "$count" > "$counter_file"
printf '%s\n' "${SMS_RELAY_ANDROID_USER_ID:-}" >> "$SMS_RELAY_STATE_DIR/watcher.user-ids"
if [ "${HARNESS_WATCHER_WAKE:-0}" = 1 ]; then
    "$REAL_SLEEP" 0.1
    worker_pid=$(cat "$SMS_RELAY_STATE_DIR/run/worker.pid" 2>/dev/null || true)
    [ -z "$worker_pid" ] || kill -USR1 "$worker_pid" >/dev/null 2>&1 || true
fi
"$REAL_SLEEP" 30
EOF

cat > "$STUB_BIN/mv" <<'EOF'
#!/bin/sh
source_path=$1
target_path=$2
"$REAL_MV" "$@" || exit $?
if [ "${HARNESS_REPLACE_ON_CLAIM:-0}" = 1 ]; then
    case "$source_path:$target_path" in
        */spool/*.json:*/run/inflight/*/*.json)
            printf '%s' replacement > "$source_path"
            ;;
    esac
fi
if [ "${HARNESS_DISABLE_ON_CLAIM:-0}" = 1 ]; then
    case "$source_path:$target_path" in
        */spool/*.json:*/run/inflight/*/*.json)
            : > "$SMS_RELAY_STATE_DIR/disabled"
            ;;
    esac
fi
EOF

cat > "$STUB_BIN/ln" <<'EOF'
#!/bin/sh
[ "${HARNESS_FAIL_LINK:-0}" != 1 ] || exit 1
exec "$REAL_LN" "$@"
EOF

cat > "$STUB_BIN/rm" <<'EOF'
#!/bin/sh
for argument in "$@"; do
    case "$argument" in
        */run/inflight/*/${HARNESS_FAIL_CLAIM_DELETE_BASENAME:-__never__}) exit 1 ;;
    esac
done
exec "$REAL_RM" "$@"
EOF

cat > "$STUB_BIN/uploader-wrapper" <<EOF
#!/bin/sh
case " $* " in
    *" --check-config "*)
        if [ "${HARNESS_FORCE_CHECK_MISSING_DEPENDENCY:-0}" = 1 ]; then
            printf '%s\n' missing_dependency >&2
            exit 1
        fi
        ;;
esac
if [ "\${HARNESS_REQUIRE_INFLIGHT:-0}" = 1 ]; then
    case " \$* " in
        *" --check-config "*) ;;
        *)
            for uploader_argument in "\$@"; do uploader_input=\$uploader_argument; done
            case "\$uploader_input" in */run/inflight/*/*.json) ;; *) exit 1 ;; esac
            ;;
    esac
fi
if [ "\${HARNESS_FAST_UPLOADER:-0}" = 1 ]; then
    case " \$* " in
        *" --check-config "*)
            printf '%s\n' ready
            exit 0
            ;;
    esac
    uploader_input=
    for uploader_argument in "\$@"; do uploader_input=\$uploader_argument; done
    case "\$($REAL_SED -n '1p' "\$uploader_input")" in
        '{bad'*) printf '%s\n' local_body_invalid; exit 1 ;;
    esac
    status=\$($REAL_SED -n '1p' "\$HARNESS_CURL_QUEUE")
    $REAL_SED '1d' "\$HARNESS_CURL_QUEUE" > "\$HARNESS_CURL_QUEUE.next"
    $REAL_MV "\$HARNESS_CURL_QUEUE.next" "\$HARNESS_CURL_QUEUE"
    upload_count_file=\$SMS_RELAY_STATE_DIR/service-upload.count
    upload_count=0
    [ ! -f "\$upload_count_file" ] || upload_count=\$(cat "\$upload_count_file")
    upload_count=\$((upload_count + 1))
    printf '%s\n' "\$upload_count" > "\$upload_count_file"
    if [ -n "\${HARNESS_DISABLE_AFTER_UPLOAD_COUNT:-}" ] && [ "\$upload_count" -ge "\$HARNESS_DISABLE_AFTER_UPLOAD_COUNT" ]; then
        : > "\$SMS_RELAY_STATE_DIR/disabled"
    fi
    if [ "\${HARNESS_STOP_WHEN_QUEUE_EMPTY:-0}" = 1 ] && [ ! -s "\$HARNESS_CURL_QUEUE" ]; then
        : > "\$SMS_RELAY_STATE_DIR/disabled"
    fi
    printf '%s\n' "\$status"
    case "\$status" in 2??) exit 0 ;; *) exit 1 ;; esac
fi
exec "$REAL_SH" "$UPLOADER" "\$@"
EOF

chmod 700 "$STUB_BIN"/*

PATH=$STUB_BIN:$PATH
export PATH

NO_MKTEMP_BIN=$HARNESS_ROOT/no-mktemp-bin
mkdir -p "$NO_MKTEMP_BIN"
for dependency in curl awk grep sed tr wc od dd date mkdir rmdir sleep rm mv chmod ln flock; do
    dependency_path=$(command -v "$dependency")
    printf '#!/bin/sh\nexec "%s" "$@"\n' "$dependency_path" > "$NO_MKTEMP_BIN/$dependency"
done
chmod 700 "$NO_MKTEMP_BIN"/*

write_config() {
    state_dir=$1
    mkdir -p "$state_dir/tmp"
    cat > "$state_dir/config.json" <<EOF
{
  "schema_version": 2,
  "enabled": true,
  "base_url": "https://relay.invalid",
  "device_id": "11111111-2222-4333-8444-555555555555",
  "hmac_secret": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlBQkNERUY",
  "source": "root_module",
  "android_user_id": 0,
  "initial_sync_mode": "new_only",
  "poll_interval_seconds": 1,
  "watcher_idle_seconds": 10,
  "max_backoff_seconds": 2,
  "sms_database_path": ""
}
EOF
}

write_mqtt_config() {
    state_dir=$1
    broker_url=$2
    topic=$3
    mkdir -p "$state_dir/tmp"
    cat > "$state_dir/config.json" <<EOF
{
  "schema_version": 2,
  "enabled": true,
  "base_url": "https://relay.invalid",
  "transport_mode": "mqtt",
  "fallback_to_http": false,
  "mqtt_broker_url": "$broker_url",
  "mqtt_topic": "$topic",
  "mqtt_username": "",
  "mqtt_password": "",
  "device_id": "11111111-2222-4333-8444-555555555555",
  "hmac_secret": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlBQkNERUY",
  "source": "root_module",
  "android_user_id": 0,
  "initial_sync_mode": "new_only",
  "poll_interval_seconds": 1,
  "watcher_idle_seconds": 10,
  "max_backoff_seconds": 2,
  "sms_database_path": ""
}
EOF
}

write_body() {
    output_path=$1
    message_id=$2
    printf '%s' "{\"source\":\"root_module\",\"client_message_id\":\"$message_id\",\"sender\":\"sender\",\"body\":\"secret-body\",\"received_at\":\"2026-08-17T00:00:00Z\"}" > "$output_path"
}

write_depth_body() {
    output_path=$1
    message_id=$2
    requested_depth=$3
    printf '%s' "{\"source\":\"root_module\",\"client_message_id\":\"$message_id\",\"metadata\":" > "$output_path"
    current_depth=1
    while [ "$current_depth" -lt "$requested_depth" ]; do
        printf '%s' '{"v":' >> "$output_path"
        current_depth=$((current_depth + 1))
    done
    printf '0' >> "$output_path"
    while [ "$current_depth" -gt 1 ]; do
        printf '}' >> "$output_path"
        current_depth=$((current_depth - 1))
    done
    printf '}' >> "$output_path"
}

set_curl_queue() {
    queue_path=$1
    shift
    : > "$queue_path"
    for status in "$@"; do printf '%s\n' "$status" >> "$queue_path"; done
}

run_uploader_capture() {
    state_dir=$1
    body_path=$2
    message_id=$3
    output_path=$4
    error_path=$5
    set +e
    SMS_RELAY_STATE_DIR=$state_dir \
    SMS_RELAY_TMP_PARENT=$state_dir/tmp \
    SMS_RELAY_RUNTIME=$STUB_BIN/runtime \
    HARNESS_CURL_QUEUE=$state_dir/curl.queue \
    HARNESS_REQUIRE_RESPONSE_DISCARD=${HARNESS_REQUIRE_RESPONSE_DISCARD:-0} \
    HARNESS_RESPONSE_BODY=${HARNESS_RESPONSE_BODY:-} \
    HARNESS_EXPECT_ENDPOINT=${HARNESS_EXPECT_ENDPOINT:-} \
    HARNESS_TRANSPORT_STATUS=${HARNESS_TRANSPORT_STATUS:-} \
        "$REAL_SH" "$UPLOADER" --config "$state_dir/config.json" --idempotency-key "$message_id" "$body_path" > "$output_path" 2> "$error_path"
    uploader_status=$?
    set -e
    return "$uploader_status"
}

assert_uploader_outcome() {
    label=$1
    state_dir=$2
    body_path=$3
    message_id=$4
    expected=$5
    output_path=$state_dir/$label.out
    error_path=$state_dir/$label.err
    run_uploader_capture "$state_dir" "$body_path" "$message_id" "$output_path" "$error_path" || true
    actual=$(cat "$output_path")
    assert_equal "$label" "$actual" "$expected"
    if grep -F 'secret-body' "$output_path" "$error_path" >/dev/null 2>&1; then fail_test "$label-sensitive-body"; fi
    if grep -F 'MDEyMzQ1' "$output_path" "$error_path" >/dev/null 2>&1; then fail_test "$label-sensitive-secret"; fi
}

assert_check_config_outcome() {
    label=$1
    state_dir=$2
    config_path=$3
    expected_status=$4
    expected_output=$5
    output_path=$state_dir/$label.out
    error_path=$state_dir/$label.err
    set +e
    SMS_RELAY_STATE_DIR=$state_dir \
    SMS_RELAY_TMP_PARENT=$state_dir/tmp \
    SMS_RELAY_RUNTIME=$STUB_BIN/runtime \
        "$REAL_SH" "$UPLOADER" --config "$config_path" --check-config > "$output_path" 2> "$error_path"
    actual_status=$?
    set -e
    assert_equal "$label-status" "$actual_status" "$expected_status"
    if [ "$expected_status" = 0 ]; then
        actual_output=$(cat "$output_path")
    else
        actual_output=$(cat "$error_path")
    fi
    assert_equal "$label-output" "$actual_output" "$expected_output"
}

assert_body_invalid() {
    label=$1
    state_dir=$2
    body_text=$3
    expected_id=$4
    body_path=$state_dir/$label.json
    printf '%s' "$body_text" > "$body_path"
    assert_uploader_outcome "$label" "$state_dir" "$body_path" "$expected_id" local_body_invalid
}

if [ "${HARNESS_SERVICE_ONLY:-0}" != 1 ] &&
   { [ "${HARNESS_ONLY:-all}" = all ] || [ "${HARNESS_ONLY:-all}" = uploader-contracts ] || [ "${HARNESS_ONLY:-all}" = response-discard ]; }; then
UPLOADER_STATE=$HARNESS_ROOT/uploader-state
mkdir -p "$UPLOADER_STATE"
write_config "$UPLOADER_STATE"
if [ "${HARNESS_ONLY:-all}" = all ]; then
VALID_BODY=$UPLOADER_STATE/valid.json
write_body "$VALID_BODY" id
valid_size=$(wc -c < "$VALID_BODY" | tr -d '[:space:]')
dd if=/dev/zero bs=1 count=$((16384 - valid_size)) 2>/dev/null | tr '\000' ' ' >> "$VALID_BODY"
set_curl_queue "$UPLOADER_STATE/curl.queue" 200
assert_uploader_outcome body-16384 "$UPLOADER_STATE" "$VALID_BODY" id 200
printf ' ' >> "$VALID_BODY"
assert_uploader_outcome body-16385 "$UPLOADER_STATE" "$VALID_BODY" id local_body_too_large

DEPTH32=$UPLOADER_STATE/depth32.json
DEPTH33=$UPLOADER_STATE/depth33.json
write_depth_body "$DEPTH32" depth32 32
write_depth_body "$DEPTH33" depth33 33
set_curl_queue "$UPLOADER_STATE/curl.queue" 200
assert_uploader_outcome depth-32 "$UPLOADER_STATE" "$DEPTH32" depth32 200
assert_uploader_outcome depth-33 "$UPLOADER_STATE" "$DEPTH33" depth33 local_body_invalid
printf '%s' '{bad' > "$UPLOADER_STATE/malformed.json"
assert_uploader_outcome malformed "$UPLOADER_STATE" "$UPLOADER_STATE/malformed.json" malformed local_body_invalid
: > "$UPLOADER_STATE/empty.json"
assert_uploader_outcome empty "$UPLOADER_STATE" "$UPLOADER_STATE/empty.json" empty local_body_invalid
assert_uploader_outcome missing "$UPLOADER_STATE" "$UPLOADER_STATE/missing.json" missing local_body_read_failed
printf '%s\n' 'PASS behavior=uploader-boundaries'

for http_status in 200 409 422 429 500; do
    write_body "$UPLOADER_STATE/http.json" http
    set_curl_queue "$UPLOADER_STATE/curl.queue" "$http_status"
    assert_uploader_outcome "http-$http_status" "$UPLOADER_STATE" "$UPLOADER_STATE/http.json" http "$http_status"
done
printf '%s\n' 'PASS behavior=uploader-http-statuses'
fi

if selected_case uploader-contracts; then
    rm -f "$UPLOADER_STATE/runtime.commands"
    write_body "$UPLOADER_STATE/signature.json" signature
    set_curl_queue "$UPLOADER_STATE/curl.queue" 200
    assert_uploader_outcome signature-single-pass "$UPLOADER_STATE" "$UPLOADER_STATE/signature.json" signature 200
    if grep -Fx sha256 "$UPLOADER_STATE/runtime.commands" >/dev/null 2>&1; then
        fail_test uploader-ran-standalone-sha256
    fi
    assert_equal uploader-hmac-count "$(grep -Fxc hmac "$UPLOADER_STATE/runtime.commands")" 1
    printf '%s\n' 'PASS behavior=uploader-signature-single-pass'

    cp "$UPLOADER_STATE/config.json" "$UPLOADER_STATE/config.original"
    "$REAL_SED" -i '/"base_url":/a\  "transport_mode": "ws",\
  "fallback_to_http": true,\
  "mqtt_broker_url": "",\
  "mqtt_topic": "",\
  "mqtt_username": "",\
  "mqtt_password": "",' "$UPLOADER_STATE/config.json"
    rm -f "$UPLOADER_STATE/runtime.commands" "$UPLOADER_STATE/curl.queue"
    write_body "$UPLOADER_STATE/ws.json" ws
    HARNESS_TRANSPORT_STATUS=201 assert_uploader_outcome ws-primary "$UPLOADER_STATE" "$UPLOADER_STATE/ws.json" ws 201
    grep -Fx transport-upload "$UPLOADER_STATE/runtime.commands" >/dev/null 2>&1 || fail_test ws-primary-not-attempted
    write_body "$UPLOADER_STATE/ws-fallback.json" ws-fallback
    set_curl_queue "$UPLOADER_STATE/curl.queue" 200
    HARNESS_TRANSPORT_STATUS= assert_uploader_outcome ws-http-fallback "$UPLOADER_STATE" "$UPLOADER_STATE/ws-fallback.json" ws-fallback 200
    mv "$UPLOADER_STATE/config.original" "$UPLOADER_STATE/config.json"
    printf '%s\n' 'PASS behavior=uploader-transport-fallback'

    MQTT_STATE=$HARNESS_ROOT/mqtt-config
    mqtt_topic_512=$(dd if=/dev/zero bs=1 count=512 2>/dev/null | tr '\000' a)
    write_mqtt_config "$MQTT_STATE" 'ws://broker.example:8083/mqtt' 'relay/v1/devices/{device_id}/messages'
    assert_check_config_outcome mqtt-ws-path "$MQTT_STATE" "$MQTT_STATE/config.json" 0 ready
    write_mqtt_config "$MQTT_STATE" 'mqtt://broker.example:1883' relay/messages
    assert_check_config_outcome mqtt-topic-contract "$MQTT_STATE" "$MQTT_STATE/config.json" 1 config_invalid
    write_mqtt_config "$MQTT_STATE" 'mqtt://broker.example:1883/path' relay/messages
    assert_check_config_outcome mqtt-tcp-path "$MQTT_STATE" "$MQTT_STATE/config.json" 1 config_invalid
    mqtt_topic_513=$(dd if=/dev/zero bs=1 count=513 2>/dev/null | tr '\000' a)
    write_mqtt_config "$MQTT_STATE" 'mqtt://broker.example:1883' "$mqtt_topic_513"
    assert_check_config_outcome mqtt-topic-513 "$MQTT_STATE" "$MQTT_STATE/config.json" 1 config_invalid
    printf '%s\n' 'PASS behavior=mqtt-config-boundaries'

    CONFIG_NEGATIVE=$UPLOADER_STATE/config-negative
    cp "$UPLOADER_STATE/config.json" "$CONFIG_NEGATIVE"
    "$REAL_SED" -i 's/"enabled": true,/"enabled": false,/' "$CONFIG_NEGATIVE"
    assert_check_config_outcome config-disabled "$UPLOADER_STATE" "$CONFIG_NEGATIVE" 1 config_disabled

    cp "$UPLOADER_STATE/config.json" "$CONFIG_NEGATIVE"
    "$REAL_SED" -i '/"enabled": true,/a\  "enabled": true,' "$CONFIG_NEGATIVE"
    assert_check_config_outcome config-duplicate "$UPLOADER_STATE" "$CONFIG_NEGATIVE" 1 config_invalid

    cp "$UPLOADER_STATE/config.json" "$CONFIG_NEGATIVE"
    "$REAL_SED" -i 's/"source": "root_module"/"unknown": "root_module"/' "$CONFIG_NEGATIVE"
    assert_check_config_outcome config-unknown "$UPLOADER_STATE" "$CONFIG_NEGATIVE" 1 config_invalid

    cp "$UPLOADER_STATE/config.json" "$CONFIG_NEGATIVE"
    "$REAL_SED" -i 's/"base_url": "https:\/\/relay.invalid"/"base_url": {"value":"https:\/\/relay.invalid"}/' "$CONFIG_NEGATIVE"
    assert_check_config_outcome config-nested "$UPLOADER_STATE" "$CONFIG_NEGATIVE" 1 config_invalid

    cp "$UPLOADER_STATE/config.json" "$CONFIG_NEGATIVE"
    "$REAL_SED" -i 's#"base_url": "https://relay.invalid"#"base_url": "https://relay.invalid/api?x=1"#' "$CONFIG_NEGATIVE"
    assert_check_config_outcome config-query "$UPLOADER_STATE" "$CONFIG_NEGATIVE" 1 config_invalid

    cp "$UPLOADER_STATE/config.json" "$CONFIG_NEGATIVE"
    "$REAL_SED" -i 's|"base_url": "https://relay.invalid"|"base_url": "https://relay.invalid/api#frag"|' "$CONFIG_NEGATIVE"
    assert_check_config_outcome config-fragment "$UPLOADER_STATE" "$CONFIG_NEGATIVE" 1 config_invalid

    cp "$UPLOADER_STATE/config.json" "$CONFIG_NEGATIVE"
    "$REAL_SED" -i 's#"base_url": "https://relay.invalid"#"base_url": "https://relay.invalid/api/"#' "$CONFIG_NEGATIVE"
    assert_check_config_outcome config-path-prefix "$UPLOADER_STATE" "$CONFIG_NEGATIVE" 0 ready
    cp "$UPLOADER_STATE/config.json" "$UPLOADER_STATE/config.original"
    cp "$CONFIG_NEGATIVE" "$UPLOADER_STATE/config.json"
    write_body "$UPLOADER_STATE/path-prefix.json" path-prefix
    set_curl_queue "$UPLOADER_STATE/curl.queue" 200
    HARNESS_EXPECT_ENDPOINT='https://relay.invalid/api/api/v1/ingest/messages' \
        run_uploader_capture "$UPLOADER_STATE" "$UPLOADER_STATE/path-prefix.json" path-prefix \
        "$UPLOADER_STATE/path-prefix.out" "$UPLOADER_STATE/path-prefix.err" || true
    assert_equal path-prefix-status "$(cat "$UPLOADER_STATE/path-prefix.out")" 200
    assert_equal path-prefix-endpoint "$(cat "$UPLOADER_STATE/curl.endpoint")" 'https://relay.invalid/api/api/v1/ingest/messages'
    mv "$UPLOADER_STATE/config.original" "$UPLOADER_STATE/config.json"

    assert_body_invalid duplicate-source "$UPLOADER_STATE" '{"source":"root_module","source":"root_module","client_message_id":"duplicate-source"}' duplicate-source
    assert_body_invalid duplicate-id "$UPLOADER_STATE" '{"source":"root_module","client_message_id":"duplicate-id","client_message_id":"duplicate-id"}' duplicate-id
    assert_body_invalid nested-only "$UPLOADER_STATE" '{"metadata":{"source":"root_module","client_message_id":"nested-only"}}' nested-only
    assert_body_invalid wrong-source "$UPLOADER_STATE" '{"source":"other","client_message_id":"wrong-source"}' wrong-source
    assert_body_invalid wrong-id "$UPLOADER_STATE" '{"source":"root_module","client_message_id":"body-id"}' wrong-id
    assert_body_invalid escaped-contract-key "$UPLOADER_STATE" '{"sou\u0072ce":"root_module","client_message_id":"escaped-contract-key"}' escaped-contract-key
    printf '%s\n' 'PASS behavior=production-parser-negatives'
fi

if selected_case response-discard; then
    write_body "$UPLOADER_STATE/response.json" response
    set_curl_queue "$UPLOADER_STATE/curl.queue" 200
    HARNESS_REQUIRE_RESPONSE_DISCARD=1 HARNESS_RESPONSE_BODY='sensitive-response-body' \
        run_uploader_capture "$UPLOADER_STATE" "$UPLOADER_STATE/response.json" response \
        "$UPLOADER_STATE/response.out" "$UPLOADER_STATE/response.err" || true
    assert_equal response-discard-status "$(cat "$UPLOADER_STATE/response.out")" 200
    assert_equal response-discard-target "$(cat "$UPLOADER_STATE/curl.output-target")" /dev/null
    if grep -F sensitive-response-body "$UPLOADER_STATE/response.out" "$UPLOADER_STATE/response.err" >/dev/null 2>&1; then
        fail_test response-body-leaked
    fi
    printf '%s\n' 'PASS behavior=response-body-discard'
fi
fi

new_service_state() {
    scenario=$1
    scenario_state=$HARNESS_ROOT/$scenario
    mkdir -p "$scenario_state/spool" "$scenario_state/tmp"
    write_config "$scenario_state"
    printf '%s' "$scenario_state"
}

run_service() {
    state_dir=$1
    shift
    service_timeout_seconds=${HARNESS_SERVICE_TIMEOUT_SECONDS:-30}
    env \
        PATH="$PATH" \
        SMS_RELAY_STATE_DIR="$state_dir" \
        SMS_RELAY_LOCK_FILE="$state_dir/worker.lock" \
        SMS_RELAY_TMP_PARENT="$state_dir/tmp" \
        SMS_RELAY_UPLOADER="$STUB_BIN/uploader-wrapper" \
        SMS_RELAY_RUNTIME="$STUB_BIN/runtime" \
        SMS_RELAY_WATCHER="$STUB_BIN/watcher" \
        SMS_RELAY_WATCHER_IDLE_SECONDS=10 \
        HARNESS_CURL_QUEUE="$state_dir/curl.queue" \
        HARNESS_STOP_WHEN_QUEUE_EMPTY=1 \
        HARNESS_REQUIRE_SNAPSHOT=1 \
        HARNESS_FAST_UPLOADER=1 \
        "$@" \
        "$REAL_SH" "$SERVICE" > "$state_dir/service.out" 2> "$state_dir/service.err" &
    service_pid=$!
    (
        "$REAL_SLEEP" "$service_timeout_seconds"
        : > "$state_dir/watchdog.timeout"
        kill -TERM "$service_pid" >/dev/null 2>&1 || true
        "$REAL_SLEEP" 1
        kill -KILL "$service_pid" >/dev/null 2>&1 || true
    ) &
    watchdog_pid=$!
    set +e
    wait "$service_pid"
    service_status=$?
    set -e
    kill "$watchdog_pid" >/dev/null 2>&1 || true
    wait "$watchdog_pid" 2>/dev/null || true
    if [ -e "$state_dir/watchdog.timeout" ]; then
        service_error=$(tail -n 1 "$state_dir/service.err" 2>/dev/null || true)
        fail_test "service-timeout last_error=$service_error"
    fi
    [ "$service_status" = 0 ] || return "$service_status"
    if grep -F 'secret-body' "$state_dir/service.out" "$state_dir/service.err" >/dev/null 2>&1; then fail_test service-sensitive-body; fi
    if grep -F 'MDEyMzQ1' "$state_dir/service.out" "$state_dir/service.err" >/dev/null 2>&1; then fail_test service-sensitive-secret; fi
    if grep -F "$state_dir" "$state_dir/service.out" "$state_dir/service.err" >/dev/null 2>&1; then fail_test service-sensitive-path; fi
}

if selected_case service-marker; then
    WATCHER_STATE=$(new_service_state watcher-lifecycle)
    set_curl_queue "$WATCHER_STATE/curl.queue"
    run_service "$WATCHER_STATE" HARNESS_REQUIRE_READER_CONFIG=1 HARNESS_READER_DISABLE_AFTER=1
    assert_equal watcher-start-count "$(cat "$WATCHER_STATE/watcher.count")" 1
    assert_equal watcher-start-user "$(cat "$WATCHER_STATE/watcher.user-ids")" 0
    printf '%s\n' 'PASS behavior=watcher-lifecycle'

    TRANSPORT_STATE=$(new_service_state service-transport-config)
    "$REAL_SED" -i '/"base_url":/a\  "transport_mode": "ws",\
  "fallback_to_http": true,\
  "mqtt_broker_url": "",\
  "mqtt_topic": "",\
  "mqtt_username": "",\
  "mqtt_password": "",' "$TRANSPORT_STATE/config.json"
    set_curl_queue "$TRANSPORT_STATE/curl.queue"
    run_service "$TRANSPORT_STATE" HARNESS_REQUIRE_READER_CONFIG=1 HARNESS_READER_DISABLE_AFTER=1
    assert_equal transport-config-reader-user "$(cat "$TRANSPORT_STATE/reader.user-ids")" 0
    printf '%s\n' 'PASS behavior=service-transport-config'

    MQTT_INVALID_STATE=$(new_service_state service-transport-mqtt-invalid)
    "$REAL_SED" -i '/"base_url":/a\  "transport_mode": "mqtt",\
  "fallback_to_http": false,\
  "mqtt_broker_url": "",\
  "mqtt_topic": "relay/v1/messages",\
  "mqtt_username": "",\
  "mqtt_password": "",' "$MQTT_INVALID_STATE/config.json"
    set_curl_queue "$MQTT_INVALID_STATE/curl.queue"
    set +e
    (run_service "$MQTT_INVALID_STATE")
    mqtt_invalid_status=$?
    set -e
    assert_equal transport-mqtt-missing-broker-status "$mqtt_invalid_status" 1
    if ! grep -F '"state":"config_invalid"' "$MQTT_INVALID_STATE/run/status.json" >/dev/null 2>&1; then
        fail_test transport-mqtt-missing-broker-status-file
    fi
    printf '%s\n' 'PASS behavior=service-transport-validation'

    WATCHER_WAKE_STATE=$(new_service_state watcher-wake)
    "$REAL_SED" -i 's/"poll_interval_seconds": 1/"poll_interval_seconds": 10/' "$WATCHER_WAKE_STATE/config.json"
    "$REAL_SED" -i 's/"watcher_idle_seconds": 10/"watcher_idle_seconds": 1/' "$WATCHER_WAKE_STATE/config.json"
    set_curl_queue "$WATCHER_WAKE_STATE/curl.queue"
    run_service "$WATCHER_WAKE_STATE" SMS_RELAY_WATCHER_IDLE_SECONDS=1 HARNESS_WATCHER_WAKE=1 HARNESS_READER_DISABLE_AFTER=2
    assert_equal watcher-wake-reader-count "$(cat "$WATCHER_WAKE_STATE/reader.count")" 2
    if ! grep -Fx '1' "$WATCHER_WAKE_STATE/sleep.arguments" >/dev/null 2>&1; then
        fail_test watcher-wake-did-not-use-watcher-idle
    fi
    printf '%s\n' 'PASS behavior=watcher-wake-low-power-wait'

    INITIAL_CONFIG_STATE=$(new_service_state initial-config-wait)
    mv "$INITIAL_CONFIG_STATE/config.json" "$INITIAL_CONFIG_STATE/config.pending"
    set_curl_queue "$INITIAL_CONFIG_STATE/curl.queue"
    run_service "$INITIAL_CONFIG_STATE" HARNESS_CREATE_CONFIG_ON_SLEEP=1 HARNESS_READER_DISABLE_AFTER=1
    assert_file "$INITIAL_CONFIG_STATE/config.json" initial-config-not-created
    assert_file "$INITIAL_CONFIG_STATE/not-configured-observed" initial-config-status-missing
    printf '%s\n' 'PASS behavior=initial-config-wait'

    WHITESPACE_STATE=$(new_service_state config-whitespace)
    "$REAL_SED" -i 's/": /"   :   /' "$WHITESPACE_STATE/config.json"
    set_curl_queue "$WHITESPACE_STATE/curl.queue"
    run_service "$WHITESPACE_STATE" HARNESS_READER_DISABLE_AFTER=1
    assert_file "$WHITESPACE_STATE/reader.count" config-whitespace-reader-not-started
    printf '%s\n' 'PASS behavior=config-whitespace'

    CONFIG_RELOAD_STATE=$(new_service_state config-wake-reload)
    "$REAL_SED" -i 's/"poll_interval_seconds": 1/"poll_interval_seconds": 9/' "$CONFIG_RELOAD_STATE/config.json"
    set_curl_queue "$CONFIG_RELOAD_STATE/curl.queue"
    run_service "$CONFIG_RELOAD_STATE" HARNESS_MUTATE_CONFIG_ON_SLEEP=1 HARNESS_READER_DISABLE_AFTER=2
    assert_equal config-reload-user-ids "$(tr '\n' ',' < "$CONFIG_RELOAD_STATE/reader.user-ids")" '0,1,'
    assert_equal config-reload-watcher-user-ids "$(tr '\n' ',' < "$CONFIG_RELOAD_STATE/watcher.user-ids")" '0,1,'
    if grep -v '^10$' "$CONFIG_RELOAD_STATE/sleep.arguments" >/dev/null 2>&1; then
        fail_test config-reload-wait-not-interruptible
    fi
    assert_absent "$CONFIG_RELOAD_STATE/run/config.changed" config-reload-marker-not-consumed
    printf '%s\n' 'PASS behavior=config-hot-reload-wake'

    CONFIG_WATCHER_IDLE_STATE=$(new_service_state config-watcher-idle)
    "$REAL_SED" -i 's/"watcher_idle_seconds": 10/"watcher_idle_seconds": 7/' "$CONFIG_WATCHER_IDLE_STATE/config.json"
    set_curl_queue "$CONFIG_WATCHER_IDLE_STATE/curl.queue"
    run_service "$CONFIG_WATCHER_IDLE_STATE" SMS_RELAY_WATCHER_IDLE_SECONDS=1 HARNESS_READER_DISABLE_AFTER=2
    if ! grep -Fx '7' "$CONFIG_WATCHER_IDLE_STATE/sleep.arguments" >/dev/null 2>&1; then
        fail_test config-watcher-idle-not-applied
    fi
    printf '%s\n' 'PASS behavior=config-watcher-idle'

    POLL_FALLBACK_STATE=$(new_service_state poll-fallback)
    "$REAL_SED" -i 's/"poll_interval_seconds": 1/"poll_interval_seconds": 6/' "$POLL_FALLBACK_STATE/config.json"
    "$REAL_SED" -i 's/"watcher_idle_seconds": 10/"watcher_idle_seconds": 1/' "$POLL_FALLBACK_STATE/config.json"
    set_curl_queue "$POLL_FALLBACK_STATE/curl.queue"
    run_service "$POLL_FALLBACK_STATE" SMS_RELAY_WATCHER="$POLL_FALLBACK_STATE/missing-watcher" HARNESS_READER_DISABLE_AFTER=2
    if ! grep -Fx '6' "$POLL_FALLBACK_STATE/sleep.arguments" >/dev/null 2>&1; then
        fail_test watcher-unavailable-did-not-use-poll-interval
    fi
    printf '%s\n' 'PASS behavior=watcher-unavailable-poll-fallback'

    READER_RETRY_STATE=$(new_service_state reader-retry-fast)
    set_curl_queue "$READER_RETRY_STATE/curl.queue"
    run_service "$READER_RETRY_STATE" SMS_RELAY_WATCHER="$READER_RETRY_STATE/missing-watcher" HARNESS_READER_FAIL_FIRST=1 HARNESS_READER_DISABLE_AFTER=3
    if grep -Fx '2' "$READER_RETRY_STATE/sleep.arguments" >/dev/null 2>&1; then
        fail_test reader-failure-used-network-backoff
    fi
    if ! grep -F '"state":"running"' "$READER_RETRY_STATE/run/status.json" >/dev/null 2>&1; then
        fail_test reader-recovery-left-stale-failure-status
    fi
    printf '%s\n' 'PASS behavior=reader-failure-fast-retry'

    LOCK_CONTENDER_STATE=$(new_service_state lock-contender-ownership)
    mkdir -p "$LOCK_CONTENDER_STATE/run"
    "$REAL_SLEEP" 30 &
    lock_owner_watcher_pid=$!
    printf '%s\n' "$lock_owner_watcher_pid" > "$LOCK_CONTENDER_STATE/run/watcher.pid"
    printf '%s\n' 0 > "$LOCK_CONTENDER_STATE/run/watcher.user"
    set_curl_queue "$LOCK_CONTENDER_STATE/curl.queue"
    run_service "$LOCK_CONTENDER_STATE" HARNESS_FLOCK_DEFAULT=1 || true
    kill -0 "$lock_owner_watcher_pid" >/dev/null 2>&1 || fail_test lock-contender-killed-active-watcher
    assert_file "$LOCK_CONTENDER_STATE/run/watcher.pid" lock-contender-removed-active-watcher-pid
    kill "$lock_owner_watcher_pid" >/dev/null 2>&1 || true
    wait "$lock_owner_watcher_pid" 2>/dev/null || true
    printf '%s\n' 'PASS behavior=lock-contender-preserves-active-worker'

    CONFIG_DISABLE_STATE=$(new_service_state config-disable-watcher)
    set_curl_queue "$CONFIG_DISABLE_STATE/curl.queue"
    run_service "$CONFIG_DISABLE_STATE" HARNESS_DISABLE_CONFIG_ON_SLEEP=1
    assert_absent "$CONFIG_DISABLE_STATE/watcher-disabled-observed" config-disable-watcher-still-running
    printf '%s\n' 'PASS behavior=config-disable-stops-watcher'

    BAD_FD_STATE=$(new_service_state bad-fd-directory-lock)
    set_curl_queue "$BAD_FD_STATE/curl.queue"
    run_service "$BAD_FD_STATE" HARNESS_FLOCK_BAD_FD=1 HARNESS_READER_DISABLE_AFTER=1
    assert_file "$BAD_FD_STATE/reader.count" bad-fd-reader-not-started
    assert_absent "$BAD_FD_STATE/worker.lock.d" bad-fd-directory-lock-retained
    printf '%s\n' 'PASS behavior=service-bad-fd-directory-lock'

    INFLIGHT_MARKER_STATE=$(new_service_state inflight-marker-between)
    mkdir -p "$INFLIGHT_MARKER_STATE/run/inflight/claim.1.1" "$INFLIGHT_MARKER_STATE/run/inflight/claim.1.2"
    write_body "$INFLIGHT_MARKER_STATE/run/inflight/claim.1.1/first.json" first
    write_body "$INFLIGHT_MARKER_STATE/run/inflight/claim.1.2/second.json" second
    set_curl_queue "$INFLIGHT_MARKER_STATE/curl.queue" 200 200
    run_service "$INFLIGHT_MARKER_STATE" HARNESS_REQUIRE_INFLIGHT=1 HARNESS_DISABLE_AFTER_UPLOAD_COUNT=1
    assert_absent "$INFLIGHT_MARKER_STATE/run/inflight/claim.1.1/first.json" inflight-marker-first-not-completed
    assert_file "$INFLIGHT_MARKER_STATE/run/inflight/claim.1.2/second.json" inflight-marker-second-was-uploaded
    assert_equal inflight-marker-upload-count "$(cat "$INFLIGHT_MARKER_STATE/service-upload.count")" 1
    printf '%s\n' 'PASS behavior=inflight-marker-between-uploads'

    CLAIM_MARKER_STATE=$(new_service_state claim-marker-before-upload)
    write_body "$CLAIM_MARKER_STATE/spool/claimed.json" claimed
    set_curl_queue "$CLAIM_MARKER_STATE/curl.queue" 200
    run_service "$CLAIM_MARKER_STATE" HARNESS_REQUIRE_INFLIGHT=1 HARNESS_DISABLE_ON_CLAIM=1
    find "$CLAIM_MARKER_STATE/run/inflight" -name claimed.json -type f | grep . >/dev/null || fail_test claim-marker-claim-lost
    assert_absent "$CLAIM_MARKER_STATE/service-upload.count" claim-marker-uploader-was-called
    assert_equal claim-marker-queue "$(cat "$CLAIM_MARKER_STATE/curl.queue")" 200
    printf '%s\n' 'PASS behavior=claim-marker-before-upload'
fi

if selected_case service-dependency; then
    DEPENDENCY_STATE=$(new_service_state missing-mktemp)
    run_service "$DEPENDENCY_STATE" PATH="$NO_MKTEMP_BIN" || true
    assert_equal service-missing-mktemp "$(cat "$DEPENDENCY_STATE/service.err")" missing_dependency
    printf '%s\n' 'PASS behavior=service-missing-dependency'
fi

if selected_case service-marker; then
    READER_STOP_STATE=$(new_service_state reader-stop)
    set_curl_queue "$READER_STOP_STATE/curl.queue"
    run_service "$READER_STOP_STATE" HARNESS_READER_BLOCK=1 HARNESS_STOP_READER_ON_START=1 || true
    assert_absent "$READER_STOP_STATE/run/reader.pid" reader-pid-retained-after-stop
    printf '%s\n' 'PASS behavior=reader-stop-lifecycle'
fi

if [ "${HARNESS_ONLY:-all}" = all ]; then
CLAIM_STATE=$(new_service_state claim-race)
write_body "$CLAIM_STATE/spool/race.json" race
set_curl_queue "$CLAIM_STATE/curl.queue" 200
run_service "$CLAIM_STATE" HARNESS_REQUIRE_INFLIGHT=1 HARNESS_REPLACE_ON_CLAIM=1
assert_file "$CLAIM_STATE/spool/race.json" claim-race-replacement-missing
assert_equal claim-race-replacement "$(cat "$CLAIM_STATE/spool/race.json")" replacement
printf '%s\n' 'PASS behavior=claim-race'

COLLISION_STATE=$(new_service_state collision)
write_body "$COLLISION_STATE/spool/collision.json" collision
mkdir -p "$COLLISION_STATE/quarantine/http_409_terminal"
printf '%s' old > "$COLLISION_STATE/quarantine/http_409_terminal/collision.json"
set_curl_queue "$COLLISION_STATE/curl.queue" 409
run_service "$COLLISION_STATE" HARNESS_REQUIRE_INFLIGHT=1
assert_equal collision-original "$(cat "$COLLISION_STATE/quarantine/http_409_terminal/collision.json")" old
assert_file "$COLLISION_STATE/quarantine/http_409_terminal/collision.json.1" collision-suffix-missing
printf '%s\n' 'PASS behavior=quarantine-collision'

LINK_STATE=$(new_service_state link-failure)
write_body "$LINK_STATE/spool/link.json" link
set_curl_queue "$LINK_STATE/curl.queue" 409
run_service "$LINK_STATE" HARNESS_FAIL_LINK=1 HARNESS_REQUIRE_INFLIGHT=1 || true
find "$LINK_STATE/run/inflight" -name link.json -type f | grep . >/dev/null || fail_test link-failure-claim-lost
printf '%s\n' 'PASS behavior=link-failure'

DELETE_STATE=$(new_service_state delete-failure)
write_body "$DELETE_STATE/spool/delete.json" delete
set_curl_queue "$DELETE_STATE/curl.queue" 422
run_service "$DELETE_STATE" HARNESS_FAIL_CLAIM_DELETE_BASENAME=delete.json HARNESS_REQUIRE_INFLIGHT=1 || true
find "$DELETE_STATE/run/inflight" -name delete.json -type f | grep . >/dev/null || fail_test delete-failure-claim-lost
assert_absent "$DELETE_STATE/quarantine/http_422_terminal/delete.json" delete-failure-target-not-cleaned
printf '%s\n' 'PASS behavior=delete-failure'

LOCAL_STATE=$(new_service_state local-invalid)
printf '%s' '{bad' > "$LOCAL_STATE/spool/local.json"
set_curl_queue "$LOCAL_STATE/curl.queue"
run_service "$LOCAL_STATE" HARNESS_READER_DISABLE_AFTER=2
assert_file "$LOCAL_STATE/quarantine/local_body_invalid/local.json" local-invalid-not-quarantined
printf '%s\n' 'PASS behavior=local-invalid'

HTTP400_STATE=$(new_service_state http-400-retry)
write_body "$HTTP400_STATE/spool/http400.json" http400
set_curl_queue "$HTTP400_STATE/curl.queue" 400
run_service "$HTTP400_STATE" HARNESS_REQUIRE_INFLIGHT=1
find "$HTTP400_STATE/run/inflight" -name http400.json -type f | grep . >/dev/null || fail_test http-400-claim-not-retained
if find "$HTTP400_STATE/quarantine" -name http400.json -type f | grep . >/dev/null; then fail_test http-400-was-quarantined; fi
grep -F '"state":"upload_failed"' "$HTTP400_STATE/run/status.json" >/dev/null 2>&1 || fail_test http-400-status-state
grep -F '"detail":"400"' "$HTTP400_STATE/run/status.json" >/dev/null 2>&1 || fail_test http-400-status-detail
printf '%s\n' 'PASS behavior=http-400-retry'

RETRY_STATE=$(new_service_state retry-recovery)
mkdir -p "$RETRY_STATE/run/inflight/preexisting"
write_body "$RETRY_STATE/run/inflight/preexisting/retry.json" retry
set_curl_queue "$RETRY_STATE/curl.queue" 429 200
run_service "$RETRY_STATE" HARNESS_REQUIRE_INFLIGHT=1
assert_absent "$RETRY_STATE/run/inflight/preexisting/retry.json" retry-claim-not-cleared
assert_absent "$RETRY_STATE/reader.count" inflight-not-processed-before-reader
printf '%s\n' 'PASS behavior=retry-recovery'
fi

if selected_case action; then
    grep -F 'SMS_RELAY_STATE_DIR' "$ACTION" >/dev/null 2>&1 || fail_test action-test-state-overrides-missing
    grep -F 'SMS_RELAY_LOCK_FILE' "$ACTION" >/dev/null 2>&1 || fail_test action-test-lock-override-missing
    grep -F 'SMS_RELAY_WAIT_SECONDS' "$ACTION" >/dev/null 2>&1 || fail_test action-test-wait-override-missing
    grep -F 'WORKER_PID_PATH' "$ACTION" >/dev/null 2>&1 || fail_test action-test-pid-path-missing
    grep -F 'kill -0' "$ACTION" >/dev/null 2>&1 || fail_test action-test-pid-probe-missing
    grep -F 'service.sh' "$ACTION" >/dev/null 2>&1 || fail_test action-test-service-identity-missing
    grep -F 'WORKER_PID_PATH' "$SERVICE" >/dev/null 2>&1 || fail_test service-test-pid-path-missing
    grep -F 'worker.pid' "$SERVICE" >/dev/null 2>&1 || fail_test service-test-pid-file-missing

    run_action() {
        action_state=$1
        action_name=$2
        shift 2
        mkdir -p "$action_state/run"
        if [ ! -f "$action_state/service.sh" ]; then
            cat > "$action_state/service.sh" <<'EOF'
#!/bin/sh
mkdir -p "$SMS_RELAY_STATE_DIR/run"
printf '%s\n' "$$" > "$SMS_RELAY_STATE_DIR/run/worker.pid"
"$REAL_SLEEP" 30
EOF
            chmod +x "$action_state/service.sh"
        fi
        action_queue=$action_state/flock.queue
        action_output=$action_state/$action_name.out
        action_error=$action_state/$action_name.err
        set +e
        env \
            PATH="$PATH" \
            SMS_RELAY_STATE_DIR="$action_state" \
            SMS_RELAY_LOCK_FILE="$action_state/worker.lock" \
            SMS_RELAY_WAIT_SECONDS=2 \
            SMS_RELAY_SERVICE_PATH="$action_state/service.sh" \
            SMS_RELAY_RUNTIME="$STUB_BIN/runtime" \
            HARNESS_FLOCK_QUEUE="$action_queue" \
            HARNESS_FLOCK_BAD_FD="${HARNESS_FLOCK_BAD_FD:-0}" \
            "$REAL_SH" "$ACTION" "$@" > "$action_output" 2> "$action_error"
        ACTION_STATUS=$?
        set -e
    }

    ACTION_STATE=$HARNESS_ROOT/action-lifecycle
    mkdir -p "$ACTION_STATE"

    run_action "$ACTION_STATE" web-default
    assert_equal action-web-status "$ACTION_STATUS" 0
    assert_equal action-web-output "$(cat "$ACTION_STATE/web-default.out")" web_opened
    grep -F 'start -a android.intent.action.VIEW -d http://127.0.0.1:35000/#action-session-token' "$ACTION_STATE/am.args" >/dev/null 2>&1 || fail_test action-web-url
    if grep -F action-session-token "$ACTION_STATE/web-default.out" "$ACTION_STATE/web-default.err" >/dev/null 2>&1; then
        fail_test action-web-token-leaked
    fi

    set_curl_queue "$ACTION_STATE/flock.queue" 0
    run_action "$ACTION_STATE" status-unconfigured status
    assert_equal action-status-unconfigured "$ACTION_STATUS" 0
    assert_equal action-status-unconfigured-output "$(cat "$ACTION_STATE/status-unconfigured.out")" not_configured

    write_config "$ACTION_STATE"
    set_curl_queue "$ACTION_STATE/flock.queue" 0
    run_action "$ACTION_STATE" status-stopped status
    assert_equal action-status-stopped "$ACTION_STATUS" 0
    assert_equal action-status-stopped-output "$(cat "$ACTION_STATE/status-stopped.out")" worker_stopped

    set_curl_queue "$ACTION_STATE/flock.queue" 1
    run_action "$ACTION_STATE" status-running status
    assert_equal action-status-running "$ACTION_STATUS" 0
    assert_equal action-status-running-output "$(cat "$ACTION_STATE/status-running.out")" enabled

    : > "$ACTION_STATE/disabled"
    set_curl_queue "$ACTION_STATE/flock.queue" 0
    run_action "$ACTION_STATE" start start
    assert_equal action-start-status "$ACTION_STATUS" 0
    assert_equal action-start-output "$(cat "$ACTION_STATE/start.out")" enabled
    assert_absent "$ACTION_STATE/disabled" action-start-marker-retained
    action_started_pid=$(cat "$ACTION_STATE/run/worker.pid")
    kill "$action_started_pid" >/dev/null 2>&1 || true
    rm -f "$ACTION_STATE/run/worker.pid"

    : > "$ACTION_STATE/disabled"
    set_curl_queue "$ACTION_STATE/flock.queue" 1
    run_action "$ACTION_STATE" busy-start start
    assert_equal action-busy-start-status "$ACTION_STATUS" 1
    assert_equal action-busy-start-output "$(cat "$ACTION_STATE/busy-start.err")" start_pending
    assert_file "$ACTION_STATE/disabled" action-busy-start-marker-cleared

    rm -f "$ACTION_STATE/disabled"
    set_curl_queue "$ACTION_STATE/flock.queue" 0
    HARNESS_FLOCK_BAD_FD=1 run_action "$ACTION_STATE" flock-bad-fd start
    assert_equal action-flock-bad-fd-status "$ACTION_STATUS" 0
    assert_equal action-flock-bad-fd-output "$(cat "$ACTION_STATE/flock-bad-fd.out")" enabled
    assert_absent "$ACTION_STATE/disabled" action-flock-bad-fd-marker-retained
    action_started_pid=$(cat "$ACTION_STATE/run/worker.pid")
    kill "$action_started_pid" >/dev/null 2>&1 || true
    rm -f "$ACTION_STATE/run/worker.pid"

    rm -f "$ACTION_STATE/disabled"
    printf '%s\n' 1 1 0 > "$ACTION_STATE/flock.queue"
    run_action "$ACTION_STATE" stop-boundary stop
    assert_equal action-stop-boundary-status "$ACTION_STATUS" 0
    assert_equal action-stop-boundary-output "$(cat "$ACTION_STATE/stop-boundary.out")" disabled
    assert_file "$ACTION_STATE/disabled" action-stop-marker-missing

    rm -f "$ACTION_STATE/disabled"
    printf '%s\n' 1 1 1 > "$ACTION_STATE/flock.queue"
    run_action "$ACTION_STATE" stop-pending stop
    assert_equal action-stop-pending-status "$ACTION_STATUS" 1
    assert_equal action-stop-pending-output "$(cat "$ACTION_STATE/stop-pending.err")" stop_pending
    assert_file "$ACTION_STATE/disabled" action-stop-pending-marker-missing

    rm -f "$ACTION_STATE/disabled"
    set_curl_queue "$ACTION_STATE/flock.queue" 0
    run_action "$ACTION_STATE" revoke revoke
    assert_equal action-revoke-status "$ACTION_STATUS" 0
    assert_equal action-revoke-output "$(cat "$ACTION_STATE/revoke.out")" credential_revocation_required
    assert_file "$ACTION_STATE/disabled" action-revoke-marker-missing
    printf '%s\n' 'PASS behavior=action-lifecycle lock-model=pid-lifecycle-with-legacy-lock-fallback'
fi

if [ "$HARNESS_ONLY" = all ]; then
    printf '%s\n' 'PASS root_posix_behavior scenarios=uploader-boundaries,production-parser,response-discard,http-classification,marker-races,claim-race,no-clobber,failures,retry-recovery,inflight-first,service-dependencies,action-lifecycle'
else
    printf '%s\n' "PASS root_posix_behavior case=$HARNESS_ONLY"
fi
