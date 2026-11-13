#!/bin/bash
# weave_cleanup.sh — LoomAI cleanup script.
#
# KEEP_SLICE=true (default): nothing is deleted.
# KEEP_SLICE=false: each Globus endpoint is deleted, then its DTN slice (a slice
# whose endpoint could not be removed is kept; FORCE_DELETE=true overrides).
set -uo pipefail
SLICE_NAME="${SLICE_NAME:-${1:-cape-globus}}"
SLICE_NAME=$(echo "$SLICE_NAME" | sed 's/[^a-zA-Z0-9-]/-/g; s/--*/-/g; s/^-//; s/-$//')
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PY="python3"
[ -x "$SCRIPT_DIR/.venv/bin/python" ] && PY="$SCRIPT_DIR/.venv/bin/python"

if [ "${KEEP_SLICE:-true}" = "true" ]; then
    echo "### PROGRESS: KEEP_SLICE=true — keeping endpoints and slices for '$SLICE_NAME'."
else
    cd "$SCRIPT_DIR" && "$PY" "$SCRIPT_DIR/provision.py" stop "$SLICE_NAME" 2>&1 || true
fi
echo "### PROGRESS: Cleanup complete."
