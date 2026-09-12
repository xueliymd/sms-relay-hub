#!/system/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
RUNTIME_DIR=$SCRIPT_DIR/../native/runtime
TEMP_ROOT=${TMPDIR:-/tmp}/sms-relay-reader-contract.$$
SENSITIVE_SECRET=reader-contract-secret-must-not-leak
SENSITIVE_BODY=reader-contract-body-must-not-leak

cleanup() {
    rm -rf "$TEMP_ROOT"
}
trap cleanup EXIT HUP INT TERM

mkdir -p "$TEMP_ROOT"

run_check() {
    config_path=$1
    output_path=$2
    (
        cd "$RUNTIME_DIR"
        go run . check-config --config "$config_path"
    ) >"$output_path" 2>&1
}

assert_redacted() {
    output_path=$1
    if grep -F "$SENSITIVE_SECRET" "$output_path" >/dev/null 2>&1 ||
       grep -F "$SENSITIVE_BODY" "$output_path" >/dev/null 2>&1; then
        printf '%s\n' "sensitive_output"
        exit 1
    fi
}

assert_accepts() {
    label=$1
    config_path=$2
    output_path=$TEMP_ROOT/$label.out
    if ! run_check "$config_path" "$output_path"; then
        assert_redacted "$output_path"
        printf '%s\n' "expected_accept:$label"
        exit 1
    fi
    assert_redacted "$output_path"
}

assert_rejects() {
    label=$1
    config_path=$2
    output_path=$TEMP_ROOT/$label.out
    if run_check "$config_path" "$output_path"; then
        assert_redacted "$output_path"
        printf '%s\n' "expected_reject:$label"
        exit 1
    fi
    assert_redacted "$output_path"
}

cat >"$TEMP_ROOT/valid.json" <<EOF
{
  "schema_version": 2,
  "enabled": false,
  "base_url": "https://relay.example.invalid",
  "device_id": "",
  "hmac_secret": "$SENSITIVE_SECRET",
  "source": "root_module",
  "android_user_id": 0,
  "initial_sync_mode": "new_only",
  "poll_interval_seconds": 15,
  "max_backoff_seconds": 300,
  "sms_database_path": ""
}
EOF

cat >"$TEMP_ROOT/old-reader.json" <<EOF
{
  "schema_version": 1,
  "enabled": false,
  "base_url": "https://relay.example.invalid",
  "device_id": "",
  "hmac_secret": "$SENSITIVE_SECRET",
  "source": "root_module",
  "reader_command": "/data/local/tmp/$SENSITIVE_BODY",
  "poll_interval_seconds": 15,
  "max_backoff_seconds": 300
}
EOF

sed 's/"max_backoff_seconds": 300/"unknown_field": 1,\n  "max_backoff_seconds": 300/' "$TEMP_ROOT/valid.json" >"$TEMP_ROOT/unknown.json"
sed 's/"initial_sync_mode": "new_only"/"initial_sync_mode": "all"/' "$TEMP_ROOT/valid.json" >"$TEMP_ROOT/all.json"
sed 's/"android_user_id": 0/"android_user_id": -1/' "$TEMP_ROOT/valid.json" >"$TEMP_ROOT/negative-user.json"
sed 's/"schema_version": 2,/"schema_version": 2,\n  "schema_version": 2,/' "$TEMP_ROOT/valid.json" >"$TEMP_ROOT/duplicate.json"

assert_accepts valid "$TEMP_ROOT/valid.json"
assert_accepts repository_example "$SCRIPT_DIR/../config.example.json"
assert_rejects old_reader "$TEMP_ROOT/old-reader.json"
assert_rejects unknown "$TEMP_ROOT/unknown.json"
assert_rejects initial_sync_all "$TEMP_ROOT/all.json"
assert_rejects negative_user "$TEMP_ROOT/negative-user.json"
assert_rejects duplicate "$TEMP_ROOT/duplicate.json"

# Android toolbox/toybox sed does not implement the GNU ``\|`` alternation
# used by the old shell parser. Simulate that portable-shell boundary here so
# the contract fails before the parser is made POSIX-compatible.
mkdir -p "$TEMP_ROOT/portable-bin"
REAL_SED=$(command -v sed)
cat >"$TEMP_ROOT/portable-bin/sed" <<EOF
#!/bin/sh
for argument in "\$@"; do
    case "\$argument" in
        *'\\|'*) exit 2 ;;
    esac
done
exec "$REAL_SED" "\$@"
EOF
chmod 755 "$TEMP_ROOT/portable-bin/sed"
cat >"$TEMP_ROOT/portable-bin/runtime" <<'EOF'
#!/bin/sh
case "${1:-}" in
    check-config) printf '%s\n' ready ;;
    nonce) printf '%s\n' AAECAwQFBgcICQoLDA0ODxAREhMUFRYX ;;
    sha256) printf '%s\n' aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa ;;
    hmac) printf '%s\n' AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8 ;;
    *) exit 1 ;;
esac
EOF
chmod 755 "$TEMP_ROOT/portable-bin/runtime"
cat >"$TEMP_ROOT/portable.json" <<'EOF'
{
  "schema_version": 2,
  "enabled": true,
  "base_url": "https://relay.example.invalid",
  "device_id": "00000000-0000-4000-8000-000000000001",
  "hmac_secret": "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8",
  "source": "root_module",
  "android_user_id": 0,
  "initial_sync_mode": "new_only",
  "poll_interval_seconds": 15,
  "max_backoff_seconds": 300
}
EOF
if ! PATH="$TEMP_ROOT/portable-bin:$PATH" \
   SMS_RELAY_RUNTIME="$TEMP_ROOT/portable-bin/runtime" \
   SMS_RELAY_TMP_PARENT="$TEMP_ROOT" \
   sh "$SCRIPT_DIR/../system/bin/sms-relay-upload" --config "$TEMP_ROOT/portable.json" --check-config \
   >"$TEMP_ROOT/portable-sed.out" 2>&1; then
    cat "$TEMP_ROOT/portable-sed.out"
    printf '%s\n' "portable_sed_config_rejected"
    exit 1
fi
grep -Fx "ready" "$TEMP_ROOT/portable-sed.out" >/dev/null 2>&1 || {
    cat "$TEMP_ROOT/portable-sed.out"
    printf '%s\n' "portable_sed_unexpected_output"
    exit 1
}

if SMS_RELAY_RUNTIME="$TEMP_ROOT/missing-runtime" "$TEMP_ROOT/missing-runtime" check-config --config "$TEMP_ROOT/valid.json" >"$TEMP_ROOT/missing.out" 2>&1; then
    printf '%s\n' "expected_reject:missing_runtime"
    exit 1
fi

run_customize_case() {
    label=$1
    abi_list=$2
    expected_status=$3
    expected_runtime=$4
    module_path=$TEMP_ROOT/module-$label
    mkdir -p "$module_path/bin" "$module_path/system/bin"
    printf '%s\n' arm64-runtime >"$module_path/bin/runtime-linux-arm64"
    printf '%s\n' arm-runtime >"$module_path/bin/runtime-linux-arm"
    touch "$module_path/service.sh" "$module_path/action.sh"
    touch "$module_path/system/bin/sms-relay-upload" "$module_path/system/bin/sms-relay-wake" "$module_path/system/bin/sms-relay-watch"
    set +e
    (
        set +u
        MODPATH=$module_path
        getprop() { printf '%s\n' "$abi_list"; }
        ui_print() { :; }
        abort() { printf '%s\n' "$1"; exit 1; }
        set_perm() { : "$@"; }
        . "$SCRIPT_DIR/../customize.sh"
    ) >"$TEMP_ROOT/$label.out" 2>&1
    customize_status=$?
    set -e
    if [ "$customize_status" != "$expected_status" ]; then
        cat "$TEMP_ROOT/$label.out"
        printf '%s\n' "customize_status:$label expected=$expected_status actual=$customize_status"
        exit 1
    fi
    if [ "$expected_status" = 0 ]; then
        expected_path=$module_path/system/bin/sms-relay-runtime
        [ -f "$expected_path" ] || { printf '%s\n' "runtime_missing:$label"; exit 1; }
        grep -Fx "$expected_runtime" "$expected_path" >/dev/null 2>&1 || {
            printf '%s\n' "runtime_selection:$label"
            exit 1
        }
    fi
}

run_customize_case apatch-arm64 'arm64-v8a,armeabi-v7a,armeabi' 0 arm64-runtime
run_customize_case legacy-arm 'armeabi-v7a,armeabi' 0 arm-runtime
run_customize_case unsupported-x86 'x86_64,x86' 1 ''
run_customize_case unsupported-plain-armeabi 'armeabi' 1 ''

printf '%s\n' "PASS root_reader_contract cases=13"
