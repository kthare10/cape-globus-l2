#!/usr/bin/env bash
# setup_demo_ui.sh - install the Globus path demo web UI on this DTN as a systemd
# service, listening on 127.0.0.1 only (reach it through an SSH tunnel).
#
# Usage:
#   sudo ./setup_demo_ui.sh --conf demo.json --user rocky [--port 8080]
# Safe to re-run: code and config are replaced and the service restarted.
set -euo pipefail

CONF=""; RUN_USER=""; PORT=8080; DEST=/opt/globus-demo-ui
while [ $# -gt 0 ]; do
  case "$1" in
    --conf) CONF="$2"; shift 2;;
    --user) RUN_USER="$2"; shift 2;;
    --port) PORT="$2"; shift 2;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "ERROR: must run as root (use sudo)" >&2; exit 2; }
[ -s "$CONF" ] && [ -n "$RUN_USER" ] || { echo "ERROR: --conf <file> and --user are required" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"

dnf install -y -q python3 python3-pip iperf3 iproute >/dev/null
install -d -m 0755 "$DEST" /etc/globus-dtn
install -m 0644 "$HERE/demo_ui/app.py" "$HERE/demo_ui/index.html" "$DEST/"
install -m 0644 "$CONF" /etc/globus-dtn/demo.json
install -m 0755 "$HERE/set_data_path.sh" /usr/local/sbin/globus-dtn-data-path
[ -x "$DEST/venv/bin/python" ] || python3 -m venv "$DEST/venv"
"$DEST/venv/bin/pip" install -q --upgrade pip >/dev/null
"$DEST/venv/bin/pip" install -q 'flask>=2.2,<4' 'globus-sdk>=3.30,<4'

cat > /etc/systemd/system/globus-demo-ui.service <<UNIT
[Unit]
Description=Globus Cape Town path demo UI (127.0.0.1:$PORT, via SSH tunnel)
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
Environment=DEMO_CONF=/etc/globus-dtn/demo.json
ExecStart=$DEST/venv/bin/python $DEST/app.py --host 127.0.0.1 --port $PORT
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable globus-demo-ui.service >/dev/null 2>&1
systemctl restart globus-demo-ui.service
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -fsS "http://127.0.0.1:$PORT/api/info" >/dev/null 2>&1 && { echo "== demo UI up on 127.0.0.1:$PORT"; exit 0; }
  sleep 1
done
echo "ERROR: demo UI did not answer on 127.0.0.1:$PORT" >&2
journalctl -u globus-demo-ui.service -n 30 --no-pager >&2 || true
exit 1
