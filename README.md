# Cape Town Globus Data Path

A FABRIC weave for the SC26 CryoEM Without Borders demo. It builds Globus Connect Server 5.4 endpoints at
**FIU** and **CIEN**. Their Globus **data** traffic to and from the SANReN Cape Town DTN runs over the
AmLight/RNP VLANs, while Globus itself reaches the endpoints on public addresses.

## How the data path is chosen

GCS takes two separate addresses per node:

| setting | set to | used by |
|---|---|---|
| `node setup --ip-address` | the public IP (FABnetv4Ext or an L3 facility port) | registration and Globus control (TCP 443) |
| `node setup --data-interface` | the data NIC facing Cape Town | GridFTP data (TCP 50000-51000) |

The Cape Town side runs Globus Connect Personal, which **opens every data connection itself**, to the address the
FABRIC endpoint advertises. Its OS routing then sends that traffic out the VLAN. Where a transfer is submitted
from (CLI, web app, SDK) makes no difference: those only talk to the Globus cloud, and the data never passes
through them.

Trade-off: an endpoint whose data interface is a private address can only exchange data with peers that can
reach that address. Use a separate endpoint for transfers with the public internet.

## Data path

Each DTN's data NIC is bridged (L2) through a FABRIC facility port onto the VLAN that reaches the Cape Town DTN.
Both ends sit in one subnet and nothing is routed.

| site | facility port | FABRIC VLAN | DTN address | Cape Town DTN |
|---|---|---|---|---|
| FIU | `AmLight-EXP-Layer2-FIU` | first free in 4000-4094 (`FIU_L2_VLAN=auto`) | `10.14.0.60/24` | `10.14.0.50` (VLAN 1400 on their side) |
| CIEN | `FP2-CIEN` | 2901 | `10.29.1.60/24` | `10.29.1.50` |

FABRIC only allows 4000-4094 on `AmLight-EXP-Layer2-FIU`, so the weave takes a VLAN from that range. The
**operators map it to Cape Town's VLAN 1400 on the switch**. The weave prints the VLAN it took
(`>>> data VLAN is ... <<<`) and records it in `results/legs.json`. To keep the same VLAN across rebuilds, set
`FIU_L2_VLAN` to that number.

## Data path choice: VLAN or public

`DATA_PATH` sets which address the endpoint advertises for data:

| `DATA_PATH` | GCS `--data-interface` | use |
|---|---|---|
| `vlan` (default) | the data NIC on the Cape Town VLAN | the demo: data on the research path |
| `public` | the public uplink (e.g. `67.17.206.74`) | transfers with Cape Town before the VLAN is mapped, and the "before" picture |

You can also switch it live from the demo UI (**Data via VLAN** / **Data via public**), or on the DTN with
`sudo globus-dtn-data-path vlan|public|show`. The switch updates the GCS node and restarts GridFTP, so the
next transfer uses the new address. Run the same transfer once with each setting to show the before/after:
red public line, then green VLAN line.

## Slices

| slice | contents |
|---|---|
| `<prefix>-fiu`, `<prefix>-cien` | one Rocky 9 DTN each: public NIC, data NIC, GCS, one mapped collection |

The **Build FIU DTN** and **Build CIEN DTN** checkboxes (`BUILD_FIU`, `BUILD_CIEN`) pick the sites. Only FIU is
built by default, while CIEN is offline, and the CIEN fields are ignored unless Build CIEN is on. From the shell,
`DTN_SITES=FIU`, `CIEN` or `FIU,CIEN` overrides the checkboxes. Each site has its own slice, so a failure at one
doesn't block the other. Status, extend and stop only touch the selected sites.

## Before you run it

1. **Globus service credential.** At app.globus.org > Settings > Developers > your project, add a service
   credential for Globus Connect Server. Copy its client UUID and its secret (shown only once). Then either:
   - enter them in the **Globus service client ID / secret** fields on the first run. The weave writes them to
     `globus_creds.env` (mode 0600, gitignored, never printed), and later runs reuse that file, so leave the
     secret blank after that; or
   - `cp globus_creds.env.example globus_creds.env`, `chmod 600` it, and fill it in by hand
     (from the shell, `GLOBUS_CREDS_FILE=<path>` points at a file kept elsewhere).

   NOTE: LoomAI keeps a run's args in `weave.json` (`active_run`) while the run is going, so the secret sits there
   until the run ends. Use the file if that matters to you.
2. **Args.** Set `GLOBUS_USERS` (`identity` or `identity=account`, comma-separated; a bare identity maps to the part before `@`) and `GLOBUS_CONTACT_EMAIL`. Without them the VMs are still
   built and the path is checked, but no endpoint is registered.
3. **FIU public address.** AmLight assigned VLAN 3002 on `AmLight-Layer3-FIU`, `67.17.206.72/29` and
   `2800:bc0:9000:3002::/64`, with the gateway at the first usable address. The defaults are `67.17.206.74/29`
   via `.73` and `2800:bc0:9000:3002::2/64` via `::1`, so the FIU endpoint is dual-stack. With a blank
   `*_PUBLIC_GW`, the gateway defaults to the first usable address. The weave checks that the outside world sees
   these addresses (no NAT). CIEN uses FABnetv4Ext (`CIEN_PUBLIC=ext`).
4. **FABRIC project tags.** You need `Net.FacilityPort.<port>` (or `Net.AllFacilityPorts`), `Net.FABNetv4Ext` for
   `ext`, and `VM.NoLimit*` for the default VM size. The weave uses the project your FABRIC token is for
   (LoomAI's selected project); set `CRYOEM_PROJECT_ID` to force another. It prints the project at the start
   and warns if one of these tags is missing.
5. **Switch mapping.** Have the FABRIC VLAN the weave picked at FIU mapped to VLAN 1400 toward Cape Town.
6. **Cape Town collection.** The demo identity needs R/W on collection `35df6f87-df63-11f1-977e-02ffe3faed53`.

## Running

```bash
./weave.sh cape-globus                  # build / resume (idempotent; never registers an endpoint twice)
ACTION=status ./weave.sh cape-globus    # route + ping to Cape Town from each DTN
ACTION=extend ./weave.sh cape-globus
KEEP_SLICE=false ./weave_cleanup.sh cape-globus   # endpoints, then slices
```

Then open `globus_transfer_demo.ipynb`. It checks route, MTU and iperf3, shows what each endpoint advertises, and
runs transfers both ways. While they run, it reads the DTN's own sockets to show which local address carried the
data.

## Demo web UI

The weave installs a small web app on each DTN (`DEMO_UI=true`, the default). It listens on `127.0.0.1` only,
so the SSH tunnel is the only way in. The weave prints the exact command, for example:

```bash
ssh -N -L 8080:127.0.0.1:8080 -i <slice key> -F ~/work/fabric_config/ssh_config rocky@<DTN mgmt IP>
# then open http://localhost:8080
```

LoomAI's tunnel feature works too (port 8080 on the `dtn` node). The page provides:

- **The path:** a diagram of the Globus cloud, the Cape Town DTN and the FABRIC DTN. The research-VLAN line
  animates while bytes flow on the data NIC. The public line stays dim, and lights up only if data goes over the
  public uplink.
- **The networks:** FABRIC and AmLight logos, and the AmLight and FABRIC network maps with the Cape Town path
  drawn on top (Cape Town → Sangano → Fortaleza → Miami on AmLight, into FABRIC at FIU/AMPATH). The route
  animates with the diagram while data flows. The notebook shows the same maps (`demo_viz.network_maps()`)
  and has the same VLAN/public data-path switch (section 4b).
- **Status:** whether Cape Town is reachable, the route (it must be the data NIC), RTT, jumbo frames, whether
  the endpoint is registered, and Globus sign-in. Buttons re-check the path and run an iperf3 baseline (needs
  `iperf3 -s` on the Cape Town side).
- **Sign in:** a Globus native-app login. Paste the code back into the page. The refresh token is kept on the
  DTN in `~/.globus-demo` (mode 0600).
- **Transfers:** pull a file or folder from Cape Town (with a browser of the Cape Town collection), push a
  generated test file, or start one in the Globus web app and click **Watch for it**.
- **Live results:** a Gb/s chart for the research VLAN vs. the public uplink, totals, the data connections, and
  a verdict when the transfer ends.

Code: `tools/demo_ui/` (Flask, plus one HTML page using Chart.js; logos and maps in `tools/demo_ui/static/`), installed by `tools/setup_demo_ui.sh` as the
`globus-demo-ui` systemd service.

## Safety

- The weave checks that no other host already uses the L2 address (`--dad`) before taking it.
- The public NIC accepts only 443 and 50000-51000. Management is never filtered or rerouted.
- `results/keys/deployment-key-<site>.json` **is** the endpoint. Keep it private (it's gitignored).
- Stop deletes each Globus endpoint **before** its slice. Otherwise only Globus support can remove the endpoint.
  If removing an endpoint fails, its slice is **kept** and stop exits non-zero. `FORCE_DELETE=true` overrides
  this, after you've removed the endpoint at app.globus.org.
- `results/legs.json` is used only by the prefix that wrote it, so stopping one prefix can't touch another's slices.

## Credits

`tools/` is adapted from *Globus Endpoint on FABRIC* (Apache 2.0, see `LICENSE`, `NOTICE`).
`setup_globus_endpoint.sh` gained `--data-interface`; `setup_data_nic.sh` is new.
