#!/usr/bin/env bash
# set_data_path.sh - choose which address this GCS node advertises for DATA.
#
#   vlan    the data NIC on the Cape Town VLAN (the point of the demo)
#   public  the public uplink - lets transfers with Cape Town run before the
#           VLAN is mapped, and gives a "before" picture (data on the internet)
#   show    print the current data interface
#
# Reads the two addresses from /etc/globus-dtn/demo.json and the service
# credential from /etc/globus-dtn/creds.env. Run as root. Restarts GridFTP so
# the next transfer uses the new address. Last line is machine-readable:
#   DATAPATH mode=vlan|public|other address=<ip>
set -euo pipefail
MODE="${1:-show}"; STATE=/etc/globus-dtn
[ "$(id -u)" -eq 0 ] || { echo "ERROR: run as root" >&2; exit 2; }
[ -s "$STATE/endpoint-id.txt" ] || { echo "ERROR: endpoint not registered on this DTN" >&2; exit 3; }
# shellcheck disable=SC1091
. "$STATE/creds.env"
export GCS_CLI_CLIENT_ID GCS_CLI_CLIENT_SECRET
GCS_CLI_ENDPOINT_ID="$(cat "$STATE/endpoint-id.txt")"; export GCS_CLI_ENDPOINT_ID

read -r DATA_IP PUBLIC_IP < <(python3 -c 'import json; d=json.load(open("/etc/globus-dtn/demo.json")); print(d["data_ip"], d.get("public_ip") or "-")')
read -r NODE_ID CUR < <(globus-connect-server node list -F json | python3 -c '
import json, sys
def walk(n):
    if isinstance(n, dict):
        if n.get("id") and "ip_addresses" in n:
            yield n
        for v in n.values(): yield from walk(v)
    elif isinstance(n, list):
        for v in n: yield from walk(v)
nodes = list(walk(json.load(sys.stdin)))
n = nodes[0] if nodes else {}
print(n.get("id", "-"), n.get("data_interface") or "-")')
[ "$NODE_ID" != "-" ] || { echo "ERROR: could not find this endpoint's node" >&2; exit 1; }

case "$MODE" in
  show)   TARGET="$CUR" ;;
  vlan)   TARGET="$DATA_IP" ;;
  public) [ "$PUBLIC_IP" != "-" ] || { echo "ERROR: no public IPv4 in demo.json" >&2; exit 2; }
          TARGET="$PUBLIC_IP" ;;
  *) echo "usage: $0 vlan|public|show" >&2; exit 2;;
esac

if [ "$TARGET" != "$CUR" ]; then
  echo "== data interface $CUR -> $TARGET"
  globus-connect-server node update "$NODE_ID" --data-interface "$TARGET"
  systemctl restart globus-gridftp-server 2>/dev/null || true
  CUR="$TARGET"
fi
case "$CUR" in "$DATA_IP") M=vlan;; "$PUBLIC_IP") M=public;; *) M=other;; esac
echo "DATAPATH mode=$M address=$CUR"
