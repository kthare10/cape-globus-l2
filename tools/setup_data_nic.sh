#!/usr/bin/env bash
# setup_data_nic.sh - address the DTN's data NIC (the one that faces the Cape Town
# DTN), persistently, without touching FABRIC management or the public uplink.
#
# The NIC is bridged through a FABRIC facility port onto the Cape Town VLAN; both
# ends sit in one subnet and nothing is routed:
#   --addr 10.14.0.60/24 --peer 10.14.0.50 --dad
# (--route/--via add a static route via a next hop on this subnet, if ever needed.)
#
# Never adds a default route: management keeps the default, the public uplink is
# policy-routed by setup_globus_dtn_net.sh, and this NIC only carries what its
# subnet (and --route) cover.
#
# --dad      refuse if another host already answers for --addr (duplicate IP).
# --trust    a CIDR whose hosts may reach anything on this host (firewalld
#            'trusted' zone source) - used for iperf3 (5201) from the Cape Town side.
#
# Safe to re-run. The last line is machine-readable:
#   DATANIC iface=.. addr=.. mtu=.. peer=.. reach=yes|no jumbo=yes|no|n/a rtt_ms=..
#
# Usage:
#   sudo ./setup_data_nic.sh --iface eth2 --addr A/len [--route CIDR --via GW]
#        [--peer IP] [--mtu 9000] [--dad] [--trust CIDR] [--profile cape-data]
set -euo pipefail

IFACE=""; ADDR=""; ROUTE=""; VIA=""; PEER=""; MTU=9000; DAD=0; TRUST=""; PROFILE="cape-data"
while [ $# -gt 0 ]; do
  case "$1" in
    --iface)   IFACE="$2"; shift 2;;
    --addr)    ADDR="$2"; shift 2;;
    --route)   ROUTE="$2"; shift 2;;
    --via)     VIA="$2"; shift 2;;
    --peer)    PEER="$2"; shift 2;;
    --mtu)     MTU="$2"; shift 2;;
    --dad)     DAD=1; shift;;
    --trust)   TRUST="$2"; shift 2;;
    --profile) PROFILE="$2"; shift 2;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done

[ "$(id -u)" -eq 0 ] || { echo "ERROR: must run as root (use sudo)" >&2; exit 2; }
[ -n "$IFACE" ] && [ -n "$ADDR" ] || { echo "ERROR: --iface and --addr are required" >&2; exit 2; }
case "$ADDR" in */*) :;; *) echo "ERROR: --addr must be CIDR, e.g. 10.14.0.60/24" >&2; exit 2;; esac
{ [ -n "$ROUTE" ] && [ -n "$VIA" ]; } || { [ -z "$ROUTE" ] && [ -z "$VIA" ]; } || {
  echo "ERROR: --route and --via go together" >&2; exit 2; }
IP="${ADDR%/*}"

dnf install -y -q iperf3 >/dev/null 2>&1 || true

# FABRIC hands a facility-port VLAN to the VM untagged (eth2) or tagged (eth2.1400).
PARENT=""; VID=""
case "$IFACE" in *.*) PARENT="${IFACE%.*}"; VID="${IFACE##*.}";; esac
if [ -n "$PARENT" ]; then
  ip link show "$PARENT" >/dev/null 2>&1 || { echo "ERROR: no parent interface $PARENT" >&2; exit 2; }
  ip link set dev "$PARENT" mtu "$MTU" 2>/dev/null || true
  ip link set dev "$PARENT" up
  ip link show "$IFACE" >/dev/null 2>&1 || ip link add link "$PARENT" name "$IFACE" type vlan id "$VID"
else
  ip link show "$IFACE" >/dev/null 2>&1 || { echo "ERROR: no interface $IFACE" >&2; exit 2; }
fi
ip link set dev "$IFACE" up

# ------------------------------------------------------- duplicate address check
# Only before the address is ours: once configured, our own host would answer.
if [ "$DAD" -eq 1 ] && ! ip -4 -o addr show dev "$IFACE" | grep -q " $IP/"; then
  if command -v arping >/dev/null 2>&1; then
    # iputils arping -D exits 0 when nobody answered, i.e. the address is free.
    if ! arping -D -q -I "$IFACE" -c 3 -w 5 "$IP"; then
      echo "ERROR: $IP is already in use on $IFACE - another host answers for it:" >&2
      arping -I "$IFACE" -c 2 -w 3 "$IP" >&2 || true
      echo "       Pick another address (and tell the Cape Town side), or free that one." >&2
      exit 3
    fi
    echo "   $IP is free on $IFACE"
  else
    echo "   WARN: arping not installed - duplicate address check skipped"
  fi
fi

# ------------------------------------------------------------- NetworkManager
release_nm_auto_profile() {
  local dev="$1" mac name
  command -v nmcli >/dev/null 2>&1 || return 0
  mac="$(cat "/sys/class/net/$dev/address" 2>/dev/null || true)"
  install -d -m 0755 /etc/NetworkManager/conf.d
  printf '[main]\nno-auto-default=interface-name:%s%s\n' "$dev" "${mac:+,mac:$mac}" \
    > "/etc/NetworkManager/conf.d/90-no-auto-default-$dev.conf"
  nmcli general reload conf >/dev/null 2>&1 || true
  nmcli -t -f NAME,DEVICE connection show 2>/dev/null | while IFS=: read -r name d; do
    case "$name" in "Wired connection "[0-9]*) ;; *) continue;; esac
    [ "$d" = "$dev" ] || \
      [ "$(nmcli -g connection.interface-name connection show "$name" 2>/dev/null)" = "$dev" ] || continue
    nmcli connection delete "$name" >/dev/null 2>&1 && echo "   removed NetworkManager auto profile '$name'"
  done
}
release_nm_auto_profile "${PARENT:-$IFACE}"

if command -v nmcli >/dev/null 2>&1; then
  if ! nmcli -t -f NAME connection show 2>/dev/null | grep -qx "$PROFILE"; then
    if [ -n "$PARENT" ]; then
      nmcli connection add type vlan con-name "$PROFILE" ifname "$IFACE" dev "$PARENT" id "$VID" autoconnect no >/dev/null
    else
      nmcli connection add type ethernet con-name "$PROFILE" ifname "$IFACE" autoconnect no >/dev/null
    fi
  fi
  nmcli connection modify "$PROFILE" connection.interface-name "$IFACE" \
    ipv4.method manual ipv4.addresses "$ADDR" ipv4.gateway "" ipv4.never-default yes \
    ipv4.routes "${ROUTE:+$ROUTE $VIA}" ipv6.method disabled \
    802-3-ethernet.mtu "$MTU" connection.autoconnect yes >/dev/null
  [ -n "$PARENT" ] && nmcli device set "$PARENT" managed yes >/dev/null 2>&1 || true
  nmcli device set "$IFACE" managed yes >/dev/null 2>&1 || true
  nmcli connection up "$PROFILE" >/dev/null 2>&1 || echo "   WARN: 'nmcli connection up $PROFILE' failed; applying with iproute2"
fi

# Apply now regardless (idempotent), in case NetworkManager did not.
ip link set dev "$IFACE" mtu "$MTU" 2>/dev/null || echo "   WARN: $IFACE would not take MTU $MTU"
ip -4 -o addr show dev "$IFACE" | grep -q " $IP/" || ip addr add "$ADDR" dev "$IFACE"
[ -n "$ROUTE" ] && ip route replace "$ROUTE" via "$VIA" dev "$IFACE"

# Loose reverse-path filtering: replies may arrive for a source this NIC routes.
cat > "/etc/sysctl.d/91-cape-data-$IFACE.conf" <<EOF
net.ipv4.conf.all.rp_filter = 2
net.ipv4.conf.${IFACE//./\/}.rp_filter = 2
EOF
sysctl -q --system || true

if [ -n "$TRUST" ] && systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --zone=trusted --add-source="$TRUST" >/dev/null 2>&1 || true
  firewall-cmd --reload >/dev/null 2>&1 || true
  echo "   firewalld: $TRUST trusted (iperf3 and Globus data from the Cape Town side)"
fi

echo "-- $IFACE"
ip -br addr show dev "$IFACE"
ip route show dev "$IFACE"

# ----------------------------------------------------------------- reachability
REACH=no; JUMBO=n/a; RTT=""
if [ -n "$PEER" ]; then
  # `|| true`: an unanswered ping is an expected state here (Cape Town not yet
  # configured), and under pipefail it would otherwise end the script.
  RTT="$(ping -c 3 -W 2 "$PEER" 2>/dev/null | awk -F/ '/^rtt|^round-trip/{print $5}' || true)"
  if [ -n "$RTT" ]; then
    REACH=yes
    echo "   route to $PEER: $(ip route get "$PEER" 2>/dev/null | head -1 || true)"
    if [ "$MTU" -gt 1500 ]; then
      if ping -c 2 -W 2 -M "do" -s $((MTU - 28)) "$PEER" >/dev/null 2>&1; then JUMBO=yes; else JUMBO=no; fi
    fi
  else
    echo "   $PEER does not answer (yet) - expected until the Cape Town side is configured"
  fi
fi
echo "DATANIC iface=$IFACE addr=$ADDR mtu=$MTU peer=${PEER:-none} reach=$REACH jumbo=$JUMBO rtt_ms=${RTT:-none}"
