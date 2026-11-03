#!/bin/bash
# systemd is the sole process owner. Safe to call repeatedly, including over SSH.
set -euo pipefail
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
action="${1:-start}"
case "$action" in
    start|restart|status) ;;
    *) echo "Usage: $0 [start|restart|status]" >&2; exit 2 ;;
esac
if [[ ! -S "$XDG_RUNTIME_DIR/bus" ]]; then
    echo "User systemd bus unavailable; restore the user manager/login session before starting Gateway." >&2
    exit 1
fi
exec systemctl --user "$action" ai-router-gateway.service --no-pager
