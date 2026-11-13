#!/usr/bin/env python3
"""Cape Town <-> FABRIC Globus data path (the weave).

Builds Globus Connect Server 5.4 endpoints on FABRIC whose GridFTP DATA traffic
to the SANReN Cape Town DTN runs over the AmLight/RNP VLANs, while Globus itself
reaches each endpoint on a public address. The trick is GCS's --data-interface:
the endpoint registers on its public IP (control, 443) but advertises its private
data-path IP for data. The Cape Town side (Globus Connect Personal) always opens
the data connections, to that advertised IP, so its own routing picks the VLAN.
Where the transfer is submitted from (CLI, portal) does not matter.

Each DTN VM's data NIC is bridged (L2) through a FABRIC facility port onto the
VLAN that reaches the Cape Town DTN: FIU via AmLight-EXP-Layer2-FIU, CIEN via
FP2-CIEN. Same subnet as the Cape Town DTN, nothing routed. FABRIC only offers
4000-4094 on AmLight-EXP-Layer2-FIU, so the weave takes a VLAN from that range
and the network operators map it to the Cape Town VLAN (1400) on their switch.

Each DTN VM: Rocky 9, a public uplink (FABnetv4Ext, or a partner L3 facility port
such as AmLight-Layer3-FIU), a data NIC as above, GCS 5.4, a firewall on the
public NIC (443 + 50000-51000 only), one mapped collection over DATA_BASE.

Actions:
    python3 provision.py start  PREFIX     # build / resume, idempotent
    python3 provision.py status PREFIX     # one-shot check
    python3 provision.py extend PREFIX     # renew every slice
    python3 provision.py stop   PREFIX     # delete endpoints, then slices

Configuration comes from environment variables set by weave.sh (see weave.json).
"""

from __future__ import annotations

import ast
import ipaddress
import json
import os
import shlex
import sys

from fabric_env import LEGS_FILE, PROJECT_NAME, RESULTS_DIR, WEAVE_DIR, get_fablib

IMAGE = "default_rocky_9"
NODE = "dtn"
DATA_NET = "DATA"
PUB_NET = "PUBLIC"
STATE_DIR = "/etc/globus-dtn"
TOOLS_DIR = os.path.join(WEAVE_DIR, "tools")
KEYS_DIR = os.path.join(RESULTS_DIR, "keys")

# Per-site defaults; every key can be overridden with <SITE>_<KEY> in the environment.
SITE_DEFAULTS = {
    # L2_VLAN "auto" = first free VLAN on the port (AmLight-EXP-Layer2-FIU: 4000-4094);
    # the operators map it to Cape Town's VLAN 1400 on their switch.
    "FIU":  {"L2_FP": "AmLight-EXP-Layer2-FIU", "L2_VLAN": "auto", "CAPE_SIDE_VLAN": 1400, "L2_IP": "10.14.0.60/24", "PEER_IP": "10.14.0.50",
             # AmLight-Layer3-FIU assignment: VLAN 3002; gateway = first usable address.
             "PUBLIC": "AmLight-Layer3-FIU", "PUBLIC_VLAN": "3002",
             "PUBLIC_ADDR": "67.17.206.74/29", "PUBLIC_ADDR6": "2800:bc0:9000:3002::2/64"},
    "CIEN": {"L2_FP": "FP2-CIEN", "L2_VLAN": "2901", "CAPE_SIDE_VLAN": 2901, "L2_IP": "10.29.1.60/24", "PEER_IP": "10.29.1.50",
             "PUBLIC": "ext"},
}


# --- config ---------------------------------------------------------------------
def env(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    return default if v is None or not v.strip() else v.strip()


def parse_users(spec: str) -> dict:
    """'alice@uni.edu=alice,bob@uni.edu=bob' -> {identity: account}."""
    out = {}
    for item in filter(None, (s.strip() for s in spec.split(","))):
        ident, _, acct = item.partition("=")
        if ident and acct:
            out[ident.strip()] = acct.strip()
    return out


def load_config() -> dict:
    sites = [s.strip().upper() for s in env("DTN_SITES", "FIU").split(",") if s.strip()]
    legs = {}
    for s in sites:
        d = SITE_DEFAULTS.get(s, {})
        legs[s] = {k.lower(): env(f"{s}_{k}", d.get(k, dv)) for k, dv in (
            ("PUBLIC", "ext"), ("PUBLIC_VLAN", "auto"), ("PUBLIC_ADDR", ""), ("PUBLIC_GW", ""),
            ("PUBLIC_ADDR6", ""), ("PUBLIC_GW6", ""),
            ("L2_FP", ""), ("L2_VLAN", ""), ("L2_IP", ""), ("PEER_IP", ""), ("HOST", ""))}
    return {
        "sites": sites,
        "legs": legs,
        "fp_bw": int(env("FP_BW", "10")),
        "data_mtu": int(env("DATA_MTU", "9000")),
        "data_nic": env("DATA_NIC", "NIC_Basic"),
        "cores": int(env("CORES", "8")),
        "ram": int(env("RAM", "32")),
        "disk": int(env("DISK", "500")),
        "lease_days": int(env("LEASE_DAYS", "14")),
        "keep_slice": env("KEEP_SLICE", "true").lower() == "true",
        "users": parse_users(env("GLOBUS_USERS")),
        "org": env("GLOBUS_ORG", "FABRIC"),
        "contact": env("GLOBUS_CONTACT_EMAIL"),
        "admin": env("GLOBUS_ADMIN"),
        "display": env("GLOBUS_DISPLAY_NAME", "CryoEM Without Borders SC26"),
        "creds": os.path.expanduser(env("GLOBUS_CREDS_FILE", os.path.join(WEAVE_DIR, "globus_creds.env"))),
        "data_base": env("DATA_BASE", "/data"),
        "cape_collection": env("CAPE_COLLECTION", "35df6f87-df63-11f1-977e-02ffe3faed53"),
        "native_client_id": env("GLOBUS_NATIVE_CLIENT_ID", "61338d24-54d5-408f-a10d-66c06b59f6d2"),
        "demo_ui": env("DEMO_UI", "true").lower() == "true",
        "demo_port": int(env("DEMO_UI_PORT", "8080")),
    }


def say(msg: str) -> None:
    print(f"### PROGRESS: {msg}", flush=True)


class LegError(Exception):
    """A problem that stops one site, not the whole run."""


def die(msg: str, code: int = 2):
    say(f"ERROR — {msg}")
    sys.exit(code)


def read_creds(path: str) -> dict:
    vals = {}
    try:
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                vals[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return vals


def globus_ready(cfg: dict) -> list:
    """Reasons Globus registration cannot run (empty = ready)."""
    why = []
    c = read_creds(cfg["creds"])
    if len(c.get("GCS_CLI_CLIENT_ID", "")) != 36 or not c.get("GCS_CLI_CLIENT_SECRET"):
        why.append(f"no valid Globus service credential in {cfg['creds']} "
                   "(copy globus_creds.env.example and fill it in)")
    if not cfg["contact"]:
        why.append("GLOBUS_CONTACT_EMAIL is empty")
    if not cfg["users"]:
        why.append("GLOBUS_USERS is empty (identity=account,...)")
    return why


# --- FABRIC helpers -------------------------------------------------------------
def vlan_list(v) -> list:
    try:
        items = ast.literal_eval(v) if isinstance(v, str) else v
    except Exception:
        items = str(v).strip("[]").replace("'", "").split(",")
    if isinstance(items, (int, str)):
        items = [items]
    out = []
    for it in items or []:
        it = str(it).strip()
        if not it:
            continue
        a, _, b = it.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


def pick_fp(fablib, site: str, vlan: str, name: str | None, l2_only: bool) -> tuple:
    """Resolve a facility port + VLAN at ``site``. Raises LegError with a listing."""
    rows = [r for r in fablib.get_facility_ports().list_facility_ports(output="list", quiet=True) or []
            if r.get("site") == site]
    listing = "\n".join(f"      {r['name']:28s} vlans {r.get('vlans')}  in use {r.get('allocated_vlans', '[]')}"
                        for r in rows) or "      (none)"
    is_l3 = lambda n: "L3" in n or "Layer3" in n
    cands = [r for r in rows if (r["name"] == name if name else not (l2_only and is_l3(r["name"])))]
    if name and not cands:
        raise LegError(f"facility port {name} is not advertised at {site}. Ports at {site}:\n{listing}")
    if str(vlan).lower() == "auto":
        if len(cands) != 1:
            raise LegError(f"VLAN 'auto' needs exactly one facility port; name it. Ports at {site}:\n{listing}")
        r = cands[0]
        taken = set(vlan_list(r.get("allocated_vlans", "[]")))
        free = [v for v in vlan_list(r.get("vlans", "[]")) if v not in taken]
        if not free:
            raise LegError(f"no free VLAN on {r['name']}")
        return r["name"], free[0]
    v = int(vlan)
    hits = [r for r in cands if v in vlan_list(r.get("vlans", "[]"))]
    if not hits and name:
        # A named port: FABRIC's advertised range can lag behind what the
        # operators provision, so let the orchestrator make the final call.
        say(f"WARNING — {name} at {site} advertises {cands[0].get('vlans')}, not VLAN {v}; "
            "requesting it anyway (the submit fails if FABRIC does not allow it).")
        return name, v
    if not hits:
        raise LegError(f"no {'L2 ' if l2_only and not name else ''}facility port at {site} advertises "
                       f"VLAN {v}. Ports at {site}:\n{listing}")
    if len(hits) > 1:
        raise LegError(f"several ports at {site} advertise VLAN {v} "
                       f"({', '.join(r['name'] for r in hits)}); set the port name.")
    r = hits[0]
    if v in set(vlan_list(r.get("allocated_vlans", "[]"))):
        raise LegError(f"VLAN {v} on {r['name']} is already allocated to another slice "
                       "(another demo slice may hold it). Free it, or use another VLAN from the "
                       "range and have the Cape Town side bring that one up.")
    return r["name"], v


def add_fp(slice_obj, name: str, site: str, vlan, bw: int, mtu: int | None = None):
    kw = dict(name=name, site=site, vlan=str(vlan), bandwidth=bw)
    if mtu:
        try:
            return slice_obj.add_facility_port(**kw, mtu=mtu)
        except TypeError:                     # older fablib: no mtu argument
            pass
    return slice_obj.add_facility_port(**kw)


def get_live(fablib, name: str):
    try:
        s = fablib.get_slice(name=name)
    except Exception:
        return None
    state = str(s.get_state())
    return None if ("Dead" in state or "Closing" in state) else s


def submit(slice_obj, what: str) -> None:
    say(f"Submitting {what} and waiting for StableOK (5-20 min)...")
    try:
        slice_obj.submit(wait=True, progress=True)
    except Exception as exc:
        raise LegError(f"submit of {what} failed: {exc}")


def renew(slice_obj, days: int) -> None:
    try:
        slice_obj.renew(days=days)
        say(f"{slice_obj.get_name()}: lease ends {slice_obj.get_lease_end()}")
    except Exception as exc:
        say(f"WARNING — lease renewal of {slice_obj.get_name()} failed: {exc}")


def run(node, cmd: str, ok=(0,)) -> tuple:
    """node.execute() that raises when the remote command fails (FABlib never does)."""
    out, err = node.execute(f"{cmd}; echo __rc=$?", quiet=False)
    rc = int(out.strip().rsplit("__rc=", 1)[-1]) if "__rc=" in out else -1
    if rc not in ok:
        raise LegError(f"`{cmd.split(';')[0][:90]}` failed (exit {rc}) on {node.get_name()}")
    return out, rc


def public_assignment(leg: dict, site: str) -> list:
    """[(family, addr/len, gateway)] for a facility-port uplink. A blank gateway is
    the subnet's first usable address (the AmLight convention). Raises LegError."""
    out = []
    for fam, a, g in (("4", leg["public_addr"], leg["public_gw"]), ("6", leg["public_addr6"], leg["public_gw6"])):
        if not a:
            continue
        try:
            iface = ipaddress.ip_interface(a)
        except ValueError as exc:
            raise LegError(f"{site}_PUBLIC_ADDR{'6' if fam == '6' else ''}: {exc}")
        if iface.network.prefixlen == iface.max_prefixlen:
            raise LegError(f"{a} needs its on-link prefix length, e.g. /29 or /64")
        gw = ipaddress.ip_address(g) if g else next(iface.network.hosts())
        if gw not in iface.network and not gw.is_link_local:
            raise LegError(f"gateway {gw} is not inside {iface.network}")
        if gw == iface.ip:
            raise LegError(f"{a} is the gateway address itself; pick another host in {iface.network}")
        out.append((fam, str(iface), str(gw)))
    return out


def slice_names(prefix: str, cfg: dict) -> dict:
    return {s: f"{prefix}-{s.lower()}" for s in cfg["sites"]}


# --- one DTN slice per site -----------------------------------------------------
def build_dtn(fablib, cfg: dict, site: str, name: str, prev_leg: dict) -> dict:
    """Create the site's DTN slice (or reuse a live one). Returns what was requested
    on the facility ports, for the report."""
    leg = cfg["legs"][site]
    s = get_live(fablib, name)
    if s is not None:
        state = str(s.get_state())
        if "Error" in state:
            raise LegError(f"slice {name} is in {state}; delete it (ACTION=stop) and re-run")
        say(f"{site}: slice {name} exists ({state}) - reconfiguring only.")
        return {k: prev_leg.get(k) for k in ("l2_fp", "l2_vlan", "public_fp", "public_vlan")}
    # Resolve every facility port before creating anything.
    l2_fp, l2_vlan = pick_fp(fablib, site, leg["l2_vlan"], leg["l2_fp"] or None, l2_only=True)
    pub_fp = pub_vlan = None
    if leg["public"].lower() != "ext":
        pub_fp, pub_vlan = pick_fp(fablib, site, leg["public_vlan"], leg["public"], l2_only=False)

    s = fablib.new_slice(name=name)
    n = s.add_node(name=NODE, site=site, host=leg["host"] or None, cores=cfg["cores"], ram=cfg["ram"],
                   disk=cfg["disk"], image=IMAGE)
    pub_if = n.add_component(model="NIC_Basic", name="pub_nic").get_interfaces()[0]
    data_if = n.add_component(model=cfg["data_nic"], name="data_nic").get_interfaces()[0]
    if pub_fp is None:
        s.add_l3network(name=PUB_NET, interfaces=[pub_if], type="IPv4Ext")
        pub_desc = "FABnetv4Ext"
    else:
        fp = add_fp(s, pub_fp, site, pub_vlan, cfg["fp_bw"])
        s.add_l2network(name=PUB_NET, interfaces=[pub_if, fp.get_interfaces()[0]])
        pub_desc = f"{pub_fp} VLAN {pub_vlan}"
    fp2 = add_fp(s, l2_fp, site, l2_vlan, cfg["fp_bw"], cfg["data_mtu"])
    s.add_l2network(name=DATA_NET, interfaces=[data_if, fp2.get_interfaces()[0]])
    say(f"{site}: {cfg['cores']}c/{cfg['ram']}G/{cfg['disk']}G on {leg['host'] or 'any host'}, public {pub_desc}, "
        f"data L2 {l2_fp} VLAN {l2_vlan} ({cfg['data_nic']})")
    say(f"{site}: >>> data VLAN is {l2_vlan} on {l2_fp} - map it to the Cape Town VLAN on the switch <<<")
    submit(s, f"{site} DTN slice {name}")
    return {"l2_fp": l2_fp, "l2_vlan": l2_vlan, "public_fp": pub_fp, "public_vlan": pub_vlan}


def configure_dtn(fablib, cfg: dict, site: str, name: str, ports: dict) -> dict:
    """Configure the DTN, then (whatever stage it reached) install the demo web UI."""
    info = _configure_dtn(fablib, cfg, site, name, ports)
    if cfg["demo_ui"] and "data_ip" in info:
        deploy_ui(fablib, cfg, site, name, info)
    return info


def deploy_ui(fablib, cfg: dict, site: str, name: str, info: dict) -> None:
    """Globus path demo UI on the DTN, 127.0.0.1 only; prints the SSH tunnel command."""
    n = fablib.get_slice(name=name).get_node(name=NODE)
    users = cfg["users"] or info.get("users") or {}
    demo = {k: info.get(k) for k in ("site", "data_ip", "data_dev", "peer_ip", "mtu", "public_ip",
                                     "public_ip6", "collection_id", "l2_fp", "l2_vlan")}
    demo.update(cape_collection=cfg["cape_collection"], native_client_id=cfg["native_client_id"],
                cape_vlan=SITE_DEFAULTS.get(site, {}).get("CAPE_SIDE_VLAN"),
                account=next(iter(users.values()), None), data_base=cfg["data_base"])
    path = os.path.join(RESULTS_DIR, f"demo-{site}.json")
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(path, "w") as fh:
        json.dump(demo, fh, indent=2)
    n.upload_file(path, "demo.json")
    say(f"{site}: installing the demo web UI (127.0.0.1:{cfg['demo_port']} on the DTN)...")
    run(n, f"sudo bash tools/setup_demo_ui.sh --conf demo.json --user {n.get_username()} "
           f"--port {cfg['demo_port']}")
    port = cfg["demo_port"]
    ssh = n.get_ssh_command()
    info["ui_tunnel"] = ssh.replace("ssh ", f"ssh -N -L {port}:127.0.0.1:{port} ", 1)
    info["ui_url"] = f"http://localhost:{port}/"
    say(f"{site}: demo UI - run this, then open {info['ui_url']}:")
    say(f"    {info['ui_tunnel']}")


def _configure_dtn(fablib, cfg: dict, site: str, name: str, ports: dict) -> dict:
    leg = cfg["legs"][site]
    s = fablib.get_slice(name=name)
    renew(s, cfg["lease_days"])
    n = s.get_node(name=NODE)
    n.upload_directory(TOOLS_DIR, ".")
    data_dev = n.get_interface(network_name=DATA_NET).get_device_name()
    pub_dev = n.get_interface(network_name=PUB_NET).get_device_name()
    info = {"site": site, "slice": name, "node": NODE, "ssh": n.get_ssh_command(),
            "mgmt_ip": str(n.get_management_ip()), **ports}

    # 1. data NIC ---------------------------------------------------------------
    addr, peer = leg["l2_ip"], leg["peer_ip"]
    extra = f" --dad --trust {ipaddress.ip_interface(addr).network}"
    mtu = cfg["data_mtu"]
    say(f"{site}: data NIC {data_dev} {addr} -> Cape Town {peer}")
    base = f"sudo bash tools/setup_data_nic.sh --iface {data_dev} --addr {addr} --peer {peer}{extra}"
    out, _ = run(n, f"{base} --mtu {mtu}")
    dn = dict(kv.split("=", 1) for kv in out.split("DATANIC ", 1)[-1].split("\n")[0].split() if "=" in kv)
    if dn.get("reach") == "yes" and dn.get("jumbo") == "no":
        say(f"{site}: MTU {mtu} does not pass to {peer} - dropping the data NIC to 1500")
        mtu = 1500
        out, _ = run(n, f"{base} --mtu {mtu}")
        dn = dict(kv.split("=", 1) for kv in out.split("DATANIC ", 1)[-1].split("\n")[0].split() if "=" in kv)
    info.update({"data_dev": data_dev, "data_ip": addr.split("/")[0], "data_addr": addr,
                 "peer_ip": peer, "mtu": mtu, "peer_reachable": dn.get("reach") == "yes",
                 "rtt_ms": dn.get("rtt_ms")})
    say(f"{site}: Cape Town {peer} {'reachable, RTT ' + str(dn.get('rtt_ms')) + ' ms' if info['peer_reachable'] else 'NOT reachable yet'}")
    run(n, "sudo bash tools/host_tune.sh")

    # 2. public uplink ----------------------------------------------------------
    if leg["public"].lower() == "ext":
        net = s.get_network(name=PUB_NET)
        if not net.get_public_ips():
            net.make_ip_publicly_routable(ipv4=[str(net.get_available_ips()[0])])
            submit(s, f"{site} public address request")
            s = fablib.get_slice(name=name)
            n = s.get_node(name=NODE)
            net = s.get_network(name=PUB_NET)
        pub_addr = f"{net.get_public_ips()[0]}/{ipaddress.ip_network(str(net.get_subnet())).prefixlen}"
        pub_gw = str(net.get_gateway())
    else:
        fams = public_assignment(leg, site)
        if not fams:
            say(f"{site}: no {site}_PUBLIC_ADDR / {site}_PUBLIC_ADDR6 for {leg['public']} yet - probing the port.")
            n.execute("sudo dnf install -y -q tcpdump >/dev/null 2>&1 || true", quiet=True)
            n.execute(f"sudo bash tools/probe_facility_port.sh --iface {pub_dev} --wait 45 --sniff 20", quiet=False)
            info["public"] = "needs-address"
            info["next"] = (f"Ask the {leg['public']} operator for an address on this VLAN, set "
                            f"{site}_PUBLIC_ADDR (and/or {site}_PUBLIC_ADDR6) as CIDR, and re-run the weave. "
                            f"Or set {site}_PUBLIC=ext to use FABnetv4Ext.")
            say(f"{site}: {info['next']}")
            return info
        v4 = next(((a, g) for f, a, g in fams if f == "4"), None)
        v6 = next(((a, g) for f, a, g in fams if f == "6"), None)
        pub_addr, pub_gw = v4 if v4 else (None, None)
        pub_addr6, pub_gw6 = v6 if v6 else (None, None)
    if leg["public"].lower() == "ext":
        pub_addr6 = pub_gw6 = None
    info["public_ip"] = pub_addr.split("/")[0] if pub_addr else None
    info["public_ip6"] = pub_addr6.split("/")[0] if pub_addr6 else None
    net_args = f"--iface {pub_dev}"
    if pub_addr:
        net_args += f" --addr {pub_addr} --gw {pub_gw}"
        say(f"{site}: public IPv4 {pub_dev} {pub_addr} via {pub_gw}")
    if pub_addr6:
        net_args += f" --addr6 {pub_addr6} --gw6 {pub_gw6}"
        say(f"{site}: public IPv6 {pub_dev} {pub_addr6} via {pub_gw6}")
    run(n, f"sudo bash tools/setup_globus_dtn_net.sh {net_args}")
    for fam, ip, target in (("4", info["public_ip"], "8.8.8.8"), ("6", info["public_ip6"], "2001:4860:4860::8888")):
        if ip:   # the address the outside sees must be ours: NAT breaks inbound Globus
            out, _ = n.execute(f"curl -{fam} -s --max-time 8 --interface {ip} https://ifconfig.me || true", quiet=True)
            seen = out.strip().splitlines()[-1] if out.strip() else ""
            say(f"{site}: IPv{fam} egress from {ip} seen as {seen or 'nothing (no route out?)'}"
                + ("" if seen in ("", ip) else " - NAT! Globus cannot reach this endpoint inbound"))

    # 3. Globus Connect Server -------------------------------------------------
    say(f"{site}: installing Globus Connect Server 5.4...")
    run(n, f"sudo bash tools/install_globus_gcs.sh --wan-iface {pub_dev}", ok=(0, 1))  # 1 = warnings
    run(n, f'sudo SSH_CONNECTION="$SSH_CONNECTION" bash tools/setup_globus_wan_firewall.sh --iface {pub_dev}')

    why = globus_ready(cfg)
    if why:
        info["globus"] = "skipped: " + "; ".join(why)
        say(f"{site}: Globus registration skipped - {'; '.join(why)}. Fix and re-run the weave.")
        return info

    n.upload_file(cfg["creds"], "globus_creds.env")
    n.execute(f"sudo install -d -m 0755 {STATE_DIR} && sudo install -m 0600 globus_creds.env "
              f"{STATE_DIR}/creds.env && shred -u globus_creds.env", quiet=True)
    display = f"{cfg['display']} ({site})"
    owner = f"{read_creds(cfg['creds'])['GCS_CLI_CLIENT_ID']}@clients.auth.globus.org"
    say(f"{site}: registering '{display}' - data interface {info['data_ip']} (the Cape Town path)")
    run(n, (f"sudo bash tools/setup_globus_endpoint.sh --creds-file {STATE_DIR}/creds.env "
            f"--state-dir {STATE_DIR} --display-name {shlex.quote(display)} "
            f"--organization {shlex.quote(cfg['org'])} --contact-email {shlex.quote(cfg['contact'])} "
            f"--owner {shlex.quote(owner)} --dont-set-advertised-owner "
            f"--public-ip {info['public_ip'] or info['public_ip6']} "
            + (f"--public-ip6 {info['public_ip6']} " if info['public_ip'] and info['public_ip6'] else "")
            + f"--data-interface {info['data_ip']}"))
    save_deployment_key(n, site)

    domains = sorted({i.split("@", 1)[1] for i in cfg["users"]})
    cmd = (f"sudo bash tools/setup_globus_storage.sh --base {shlex.quote(cfg['data_base'])} "
           f"--gateway-name {shlex.quote(display + ' storage')} --collection-name {shlex.quote(display)} "
           f"--creds-file {STATE_DIR}/creds.env --state-dir {STATE_DIR}")
    cmd += "".join(f" --domain {shlex.quote(d)}" for d in domains)
    cmd += "".join(f" --map {shlex.quote(i + '=' + a)}" for i, a in cfg["users"].items())
    if cfg["admin"]:
        cmd += f" --admin {shlex.quote(cfg['admin'])}"
    run(n, cmd)

    cat = lambda p: n.execute(f"sudo cat {p} 2>/dev/null", quiet=True)[0].strip()
    info["endpoint_id"] = cat(f"{STATE_DIR}/endpoint-id.txt")
    info["collection_id"] = cat(f"{STATE_DIR}/collection-id.txt")
    try:
        info["domain"] = json.loads(cat("/var/lib/globus-connect-server/info.json"))["domain_name"]
    except Exception:
        info["domain"] = None
    info["display_name"] = display
    info["data_base"] = cfg["data_base"]
    info["users"] = cfg["users"]
    info["globus"] = "ready"
    return info


def save_deployment_key(n, site: str) -> None:
    """deployment-key.json IS the endpoint; keep a copy off the VM (0600, gitignored)."""
    os.makedirs(KEYS_DIR, exist_ok=True)
    dst = os.path.join(KEYS_DIR, f"deployment-key-{site}.json")
    n.execute(f"sudo install -m 0600 -o $(id -un) {STATE_DIR}/deployment-key.json dk.json", quiet=True)
    old = os.umask(0o077)
    try:
        n.download_file(dst, "dk.json")
    finally:
        os.umask(old)
        n.execute("shred -u dk.json", quiet=True)
    say(f"{site}: deployment key saved to results/keys/{os.path.basename(dst)} - keep it private")


# --- actions --------------------------------------------------------------------
def read_previous(prefix: str) -> dict:
    """results/legs.json, but only if it was written for this prefix - another
    prefix's state must never steer status, extend or (above all) stop."""
    try:
        with open(LEGS_FILE) as fh:
            res = json.load(fh)
    except Exception:
        return {}
    if res.get("prefix") != prefix:
        say(f"NOTE — {LEGS_FILE} belongs to prefix '{res.get('prefix')}', not '{prefix}'; ignoring it.")
        return {}
    return res


def write_results(res: dict) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(LEGS_FILE, "w") as fh:
        json.dump(res, fh, indent=2, default=str)


def start(prefix: str) -> None:
    cfg = load_config()
    if not cfg["sites"]:
        die("DTN_SITES is empty")
    for s in cfg["sites"]:
        leg = cfg["legs"][s]
        if not (leg["l2_fp"] and leg["l2_vlan"] and leg["l2_ip"] and leg["peer_ip"]):
            die(f"{s} needs {s}_L2_FP, {s}_L2_VLAN, {s}_L2_IP and {s}_PEER_IP")
    for w in globus_ready(cfg):
        say(f"NOTE — {w}; endpoints will be built but not registered with Globus.")

    fablib = get_fablib()
    names = slice_names(prefix, cfg)
    prev = read_previous(prefix)
    say(f"Project {PROJECT_NAME} | DTN sites {', '.join(cfg['sites'])}")
    res = {"prefix": prefix, "legs": {}}

    ok = True
    for site in cfg["sites"]:
        try:
            ports = build_dtn(fablib, cfg, site, names[site], prev.get("legs", {}).get(site, {}))
            res["legs"][site] = configure_dtn(fablib, cfg, site, names[site], ports)
        except LegError as exc:
            ok = False
            say(f"{site}: ERROR — {exc}")
            res["legs"][site] = {"site": site, "slice": names[site], "error": str(exc)}
        except Exception as exc:              # keep going: one site must not block the other
            ok = False
            say(f"{site}: ERROR — {type(exc).__name__}: {exc}")
            res["legs"][site] = {"site": site, "slice": names[site], "error": repr(exc)}
        write_results(res)

    say("---------------------------------------------------------------")
    for site, leg in res["legs"].items():
        if "error" in leg:
            say(f"{site}: FAILED - {leg['error'].splitlines()[0]}")
            continue
        say(f"{site}: data {leg['data_addr']} on {leg.get('l2_fp')} VLAN {leg.get('l2_vlan')} "
            f"(MTU {leg['mtu']}) -> Cape Town {leg['peer_ip']} "
            f"{'OK' if leg['peer_reachable'] else 'not reachable yet'}; public {leg.get('public_ip') or leg.get('public_ip6') or leg.get('public')}; "
            f"Globus {leg.get('globus', '-')}")
        if leg.get("collection_id"):
            say(f"{site}: collection https://app.globus.org/file-manager?origin_id={leg['collection_id']}")
    if ok:
        say("Next: open globus_transfer_demo.ipynb.")
    sys.exit(0 if ok else 1)


def status(prefix: str) -> None:
    fablib = get_fablib()
    res = read_previous(prefix)
    for site, leg in res.get("legs", {}).items():
        s = get_live(fablib, leg["slice"])
        if s is None:
            print(f"{site}: slice {leg['slice']} not found")
            continue
        print(f"{site}: {s.get_state()}, lease ends {s.get_lease_end()}")
        if leg.get("peer_ip"):
            n = s.get_node(name=NODE)
            out, _ = n.execute(f"ip route get {leg['peer_ip']} | head -1; "
                               f"ping -c 3 -W 2 {leg['peer_ip']} | tail -2", quiet=True)
            print(out)


def extend(prefix: str) -> None:
    fablib, res = get_fablib(), read_previous(prefix)
    days = int(env("LEASE_DAYS", "14"))
    names = [leg["slice"] for leg in res.get("legs", {}).values()]
    for name in names:
        s = get_live(fablib, name)
        if s:
            renew(s, days)


def remove_endpoint(n, site: str) -> None:
    """Detach the node and delete its Globus endpoint. Raises unless both succeeded
    (or there was never an endpoint) - the caller must not delete the VM otherwise."""
    out, _ = n.execute(f"sudo test -s {STATE_DIR}/deployment-key.json && echo HASKEY || echo NOKEY",
                       quiet=True)
    if "HASKEY" not in out:
        if "NOKEY" not in out:
            raise LegError(f"could not check {STATE_DIR}/deployment-key.json on the VM")
        return                                  # never registered: nothing to remove
    say(f"{site}: removing the Globus endpoint first...")
    pre = (f"sudo bash -c '. {STATE_DIR}/creds.env && export GCS_CLI_CLIENT_ID "
           f"GCS_CLI_CLIENT_SECRET && cd {STATE_DIR} && ")
    run(n, pre + "globus-connect-server node cleanup'")
    run(n, pre + f"globus-connect-server endpoint cleanup --deployment-key "
                 f"{STATE_DIR}/deployment-key.json --agree-to-delete-endpoint'")
    # Mark it gone so a re-run of stop does not try again.
    n.execute(f"sudo mv {STATE_DIR}/deployment-key.json {STATE_DIR}/deployment-key.deleted.json",
              quiet=True)


def stop(prefix: str) -> None:
    """Delete each Globus endpoint BEFORE its slice; a slice whose endpoint could not
    be removed is kept (else only Globus support can delete the endpoint).
    FORCE_DELETE=true deletes it anyway."""
    fablib, res = get_fablib(), read_previous(prefix)
    cfg = load_config()
    names = slice_names(prefix, cfg)
    force = env("FORCE_DELETE", "false").lower() == "true"
    failed = False
    for site in cfg["sites"]:
        name = res.get("legs", {}).get(site, {}).get("slice", names[site])
        s = get_live(fablib, name)
        if s is None:
            continue
        try:
            remove_endpoint(s.get_node(name=NODE), site)
        except Exception as exc:
            if not force:
                failed = True
                say(f"{site}: ERROR — endpoint removal failed ({exc}). Keeping slice {name} so the "
                    "endpoint can still be cleaned up from the VM. Fix and re-run stop, or delete "
                    "the endpoint at app.globus.org and re-run with FORCE_DELETE=true.")
                continue
            say(f"{site}: WARNING — endpoint removal failed ({exc}); FORCE_DELETE=true, deleting anyway.")
        say(f"{site}: deleting slice {name}")
        s.delete()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    actions = {"start": start, "status": status, "extend": extend, "stop": stop}
    args = sys.argv[1:]
    action = args.pop(0) if args and args[0] in actions else "start"
    actions[action](args[0] if args else env("SLICE_NAME", "cape-globus"))
