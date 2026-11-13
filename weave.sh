#!/bin/bash
# Cape Town Globus Data Path — bring-up (LoomAI run script).
#
# LoomAI's Run button calls this with the slice prefix as $1 and exports the
# weave.json args as env vars. It builds (or resumes) one Globus endpoint VM
# per DTN site, then EXITS. Re-running is safe: existing
# slices are reconfigured, never re-created, and Globus endpoints are never
# registered twice.
#
# Manual use:
#   ./weave.sh cape-globus                   # build / resume
#   ACTION=status ./weave.sh cape-globus     # one-shot check
#   ACTION=extend ./weave.sh cape-globus     # renew every slice
#   ACTION=stop   ./weave.sh cape-globus     # endpoints, then slices
set -uo pipefail

SLICE_NAME="${SLICE_NAME:-${1:-cape-globus}}"
SLICE_NAME=$(echo "$SLICE_NAME" | sed 's/[^a-zA-Z0-9-]/-/g; s/--*/-/g; s/^-//; s/-$//')
export SLICE_NAME
ACTION="${ACTION:-start}"
export KEEP_SLICE="${KEEP_SLICE:-true}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY="python3"
[ -x "$SCRIPT_DIR/.venv/bin/python" ] && PY="$SCRIPT_DIR/.venv/bin/python"
chmod +x "$SCRIPT_DIR"/tools/*.sh 2>/dev/null || true

on_stop() {
    echo "### PROGRESS: Stop requested — slices are left as they are (re-run to resume)."
    exit 0
}
trap on_stop SIGTERM SIGINT

if [ "$ACTION" = "start" ]; then
    echo "### PROGRESS: === Cape Town Globus Data Path ==="
    echo "### PROGRESS: Prefix: $SLICE_NAME"
fi

cd "$SCRIPT_DIR" || exit 1
"$PY" "$SCRIPT_DIR/provision.py" "$ACTION" "$SLICE_NAME" &
wait $!
exit $?
