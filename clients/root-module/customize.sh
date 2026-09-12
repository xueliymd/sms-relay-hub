#!/system/bin/sh

# APatch sources this file into its installer; do not change global shell options.
ABI_LIST=$(getprop ro.product.cpu.abilist 2>/dev/null)
case ",$ABI_LIST," in
    *,arm64-v8a,*) runtime_name=runtime-linux-arm64 ;;
    *,armeabi-v7a,*) runtime_name=runtime-linux-arm ;;
    *)
        ui_print "! Unsupported Android ABI: $ABI_LIST"
        abort "unsupported_abi"
        ;;
esac

runtime_source=$MODPATH/bin/$runtime_name
runtime_target=$MODPATH/system/bin/sms-relay-runtime
[ -f "$runtime_source" ] || abort "runtime_unavailable"
mkdir -p "$MODPATH/system/bin" || abort "install_failed"
cp -f "$runtime_source" "$runtime_target" || abort "install_failed"

set_perm "$MODPATH/service.sh" 0 0 0755 ""
set_perm "$MODPATH/action.sh" 0 0 0755 ""
set_perm "$MODPATH/customize.sh" 0 0 0755 ""
set_perm "$MODPATH/uninstall.sh" 0 0 0755 ""
set_perm "$MODPATH/system/bin/sms-relay-upload" 0 0 0755 ""
set_perm "$MODPATH/system/bin/sms-relay-wake" 0 0 0755 ""
set_perm "$MODPATH/system/bin/sms-relay-watch" 0 0 0755 ""
set_perm "$runtime_target" 0 0 0755 ""

ui_print "- Installed SMS Relay runtime for $runtime_name"
