#!/usr/bin/env python3
"""Globus Cape Town path - demo web UI.

Runs ON the DTN, bound to 127.0.0.1 only: reach it through an SSH tunnel
(the weave prints the command). It reads the DTN's own GridFTP sockets, so the
"which path carried the bytes" chart needs no FABRIC API calls.

Config: /etc/globus-dtn/demo.json (written by the weave, no secrets).
Globus tokens: ~/.globus-demo/tokens.json (0600), from a native-app login done in the UI.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone

import globus_sdk
from flask import Flask, Response, jsonify, request, send_from_directory

CONF_FILE = os.environ.get("DEMO_CONF", "/etc/globus-dtn/demo.json")
STATE = os.path.expanduser("~/.globus-demo")
TOKENS = os.path.join(STATE, "tokens.json")
TRANSFER_RS = "transfer.api.globus.org"
TRANSFER_SCOPE = "urn:globus:auth:scope:transfer.api.globus.org:all"
HERE = os.path.dirname(os.path.abspath(__file__))
SS = ["ss", "-Htni", "state", "established",
      "( sport >= :50000 and sport <= :51000 ) or ( dport >= :50000 and dport <= :51000 )"]
DONE = ("SUCCEEDED", "FAILED")

app = Flask(__name__, static_folder=None)
_auth = {"client": None}


# --- config / helpers ---------------------------------------------------------
def conf() -> dict:
    with open(CONF_FILE) as fh:
        return json.load(fh)


def sh(cmd: str, timeout: int = 30) -> str:
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return ""


def err(msg: str, code: int = 400):
    return jsonify({"error": msg}), code


# --- Globus auth --------------------------------------------------------------
def auth_client() -> globus_sdk.NativeAppAuthClient:
    if _auth["client"] is None:
        _auth["client"] = globus_sdk.NativeAppAuthClient(conf()["native_client_id"])
    return _auth["client"]


def requested_scopes() -> str:
    coll = conf().get("collection_id")
    # A GCS 5 mapped collection needs its own data_access consent; Cape Town's GCP one does not.
    return TRANSFER_SCOPE + (f"[*https://auth.globus.org/scopes/{coll}/data_access]" if coll else "")


def save_tokens(resp) -> None:
    t = resp.by_resource_server[TRANSFER_RS]
    os.makedirs(STATE, mode=0o700, exist_ok=True)
    old = {}
    if os.path.exists(TOKENS):
        with open(TOKENS) as fh:
            old = json.load(fh)
    t.setdefault("refresh_token", old.get("refresh_token"))
    fd = os.open(TOKENS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(t, fh)


def tc() -> globus_sdk.TransferClient | None:
    if not os.path.exists(TOKENS):
        return None
    with open(TOKENS) as fh:
        t = json.load(fh)
    authz = globus_sdk.RefreshTokenAuthorizer(
        t["refresh_token"], auth_client(), access_token=t["access_token"],
        expires_at=t["expires_at_seconds"], on_refresh=save_tokens)
    return globus_sdk.TransferClient(authorizer=authz)


@app.post("/api/login/start")
def login_start():
    auth_client().oauth2_start_flow(requested_scopes=requested_scopes(), refresh_tokens=True)
    return jsonify({"url": auth_client().oauth2_get_authorize_url()})


@app.post("/api/login/finish")
def login_finish():
    code = (request.json or {}).get("code", "").strip()
    if not code:
        return err("no code")
    try:
        save_tokens(auth_client().oauth2_exchange_code_for_tokens(code))
    except globus_sdk.GlobusAPIError as e:
        return err(f"Globus refused the code: {e.message}")
    except Exception as e:                      # e.g. flow not started in this process
        return err(f"login failed: {e} - click Sign in again")
    return jsonify({"ok": True})


@app.post("/api/logout")
def logout():
    if os.path.exists(TOKENS):
        os.remove(TOKENS)
    return jsonify({"ok": True})


# --- status -------------------------------------------------------------------
@app.get("/api/info")
def info():
    c = conf()
    return jsonify({**c, "logged_in": os.path.exists(TOKENS)})


@app.get("/api/path")
def path():
    c = conf()
    peer = c["peer_ip"]
    route = sh(f"ip route get {peer} | head -1")
    dev = (re.search(r"\bdev (\S+)", route) or [None, None])[1]
    ping = sh(f"ping -c 3 -W 2 -q {peer}")
    rtt = re.search(r"= [\d.]+/([\d.]+)/", ping)
    mtu = int(c.get("mtu") or 1500)
    jumbo = None
    if rtt and mtu > 1500:
        jumbo = subprocess.run(f"ping -c 2 -W 2 -M do -s {mtu - 28} {peer}", shell=True,
                               capture_output=True).returncode == 0
    return jsonify({"peer": peer, "route_dev": dev, "on_data_nic": dev == c.get("data_dev"),
                    "reachable": bool(rtt), "rtt_ms": float(rtt[1]) if rtt else None,
                    "mtu": mtu, "jumbo": jumbo})


@app.post("/api/iperf")
def iperf():
    c = conf()
    out = sh(f"iperf3 -c {c['peer_ip']} -B {c['data_ip']} -P 8 -t 15 -J", timeout=40)
    try:
        doc = json.loads(out)
        if doc.get("error"):
            return err(f"iperf3: {doc['error']} - is `iperf3 -s` running on the Cape Town DTN?")
        return jsonify({"gbps": round(doc["end"]["sum_received"]["bits_per_second"] / 1e9, 2)})
    except Exception:
        return err("iperf3 gave no result - is `iperf3 -s` running on the Cape Town DTN?")


# --- Globus actions -----------------------------------------------------------
def need_tc():
    client = tc()
    if client is None:
        raise PermissionError("sign in to Globus first")
    if not conf().get("collection_id"):
        raise PermissionError("this DTN's Globus endpoint is not registered yet (re-run the weave "
                              "with Globus credentials)")
    return client


@app.get("/api/cape/ls")
def cape_ls():
    try:
        client = need_tc()
        p = request.args.get("path", "/")
        items = [{"name": e["name"], "type": e["type"], "size": e["size"]}
                 for e in client.operation_ls(conf()["cape_collection"], path=p)]
        return jsonify({"path": p, "items": items})
    except PermissionError as e:
        return err(str(e), 401)
    except globus_sdk.GlobusAPIError as e:
        return err(f"Globus: {e.message}")


@app.post("/api/transfer")
def transfer():
    try:
        client = need_tc()
    except PermissionError as e:
        return err(str(e), 401)
    c, body = conf(), request.json or {}
    acct, cape, coll = c["account"], c["cape_collection"], c["collection_id"]
    try:
        if body.get("direction") == "push":
            gb = max(1, min(int(body.get("size_gb", 10)), 200))
            fname = f"sc26-{c['site'].lower()}-{gb}G.bin"
            local = f"{c['data_base']}/{acct}/{fname}"
            subprocess.run(["sudo", "-u", acct, "bash", "-c",
                            f"[ -s {local} ] || fallocate -l {gb}G {local} || head -c {gb}G /dev/zero > {local}"],
                           check=True, timeout=300)
            dst_dir = c.get("cape_dst_dir", "/from-fabric/")
            try:
                client.operation_mkdir(cape, dst_dir)
            except globus_sdk.TransferAPIError as e:
                if "Exists" not in str(e.code):
                    raise
            td = globus_sdk.TransferData(source_endpoint=coll, destination_endpoint=cape,
                                         label=f"{c['site']} to Cape Town (SC26)", verify_checksum=True)
            td.add_item(f"/{acct}/{fname}", dst_dir + fname)
        else:
            src = body.get("path") or "/"
            is_dir = src.endswith("/")
            dst = f"/{acct}/from-cape/" + ("" if is_dir else os.path.basename(src))
            td = globus_sdk.TransferData(source_endpoint=cape, destination_endpoint=coll,
                                         label=f"Cape Town to {c['site']} (SC26)", verify_checksum=True)
            td.add_item(src, dst, recursive=is_dir)
        return jsonify({"task_id": client.submit_transfer(td)["task_id"]})
    except globus_sdk.GlobusAPIError as e:
        return err(f"Globus: {e.message}")
    except subprocess.SubprocessError as e:
        return err(f"could not create the test file: {e}")


@app.get("/api/tasks/recent")
def recent():
    """Newest transfer since `since` whose two ends are exactly Cape Town and this DTN."""
    try:
        client = need_tc()
    except PermissionError as e:
        return err(str(e), 401)
    c = conf()
    since = datetime.fromisoformat(request.args["since"].replace("Z", "+00:00"))
    pair = {c["cape_collection"], c["collection_id"]}
    hits = []
    for t in client.task_list(limit=50, filter="type:TRANSFER/status:ACTIVE,SUCCEEDED"):
        started = datetime.fromisoformat(t["request_time"].replace("Z", "+00:00"))
        if started >= since and {t["source_endpoint_id"], t["destination_endpoint_id"]} == pair:
            hits.append((started, t["task_id"]))
    return jsonify({"task_id": max(hits)[1] if hits else None})


# --- live socket stream ---------------------------------------------------------
def _ip(ep: str) -> str:
    return ep.rsplit(":", 1)[0].strip("[]").replace("::ffff:", "")


def data_conns() -> dict:
    out = subprocess.run(SS, capture_output=True, text=True).stdout
    conns, cur = {}, None
    for line in out.splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            f = line.split()
            cur = (f[2], f[3]) if len(f) >= 4 else None
            if cur:
                conns[cur] = 0
        elif cur:
            got = [int(x) for x in re.findall(r"bytes_(?:received|acked):(\d+)", line)]
            conns[cur] = max(got or [0])
    return conns


def path_of(c: dict, local_ip: str) -> str:
    if local_ip == c["data_ip"]:
        return "data"
    if local_ip in (c.get("public_ip"), c.get("public_ip6")):
        return "public"
    return "other"


@app.get("/api/stream")
def stream():
    """Server-sent events: per-path bytes on this DTN's GridFTP sockets (+ task status)."""
    c = conf()
    task_id = request.args.get("task_id") or None
    client = tc() if task_id else None

    def gen():
        seen, t0 = {}, time.time()
        while time.time() - t0 < 3600:
            for k, b in data_conns().items():
                seen[k] = max(seen.get(k, 0), b)
            totals = {"data": 0, "public": 0, "other": 0}
            for (loc, _), b in seen.items():
                totals[path_of(c, _ip(loc))] += b
            ev = {"t": round(time.time() - t0, 1), "totals": totals,
                  "conns": [{"local": _ip(l), "remote": _ip(r), "bytes": b, "path": path_of(c, _ip(l))}
                            for (l, r), b in sorted(seen.items())]}
            if client:
                try:
                    t = client.get_task(task_id)
                    ev.update(status=t["status"], nice=t.get("nice_status"),
                              bytes=t["bytes_transferred"],
                              rate=t.get("effective_bytes_per_second") or 0, label=t.get("label"))
                    ev["done"] = t["status"] in DONE or t.get("nice_status") in ("PERMISSION_DENIED",
                                                                                   "ENDPOINT_ERROR")
                except globus_sdk.GlobusAPIError as e:
                    ev.update(error=e.message, done=True)
            yield f"data: {json.dumps(ev)}\n\n"
            if ev.get("done"):
                return
            time.sleep(2)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/")
def index():
    return send_from_directory(HERE, "index.html")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8080)
    a = ap.parse_args()
    app.run(host=a.host, port=a.port, threaded=True)
