"""Demo visuals for globus_transfer_demo.ipynb (run in the notebook kernel).

- path_map():     world map - AmLight, the Cape Town <-> FABRIC data path, the DTNs
- network_maps(): the AmLight and FABRIC network maps with the Cape Town path highlighted
- LivePathPlot:   live chart during a transfer - Gb/s and bytes per path (data NIC vs public)
- path_summary(): one-glance result card after a transfer
"""

from __future__ import annotations

import os
import time

import matplotlib.pyplot as plt

BLUE, RED, GREEN, GREY, ORANGE, PURPLE = "#2471a3", "#c0392b", "#1e8449", "#7f8c8d", "#ca6f1e", "#7d3c98"
PATH_COLORS = {"data NIC (Cape Town path)": GREEN, "public uplink": RED, "other": GREY}

# AmLight points of presence and links (from the AmLight network map; city coordinates).
AMLIGHT_POPS = {
    "Atlanta": (33.75, -84.39), "Jacksonville": (30.33, -81.66), "Boca Raton": (26.37, -80.13),
    "Miami": (25.76, -80.19), "San Juan": (18.47, -66.11), "Panama City": (8.98, -79.52),
    "Fortaleza": (-3.73, -38.53), "Sao Paulo": (-23.55, -46.63), "Porto Alegre": (-30.03, -51.23),
    "Buenos Aires": (-34.60, -58.38), "Santiago": (-33.45, -70.67), "Sines": (37.96, -8.87),
    "Sangano": (-9.59, 13.24), "Cape Town": (-33.92, 18.42),
}
AMLIGHT_LINKS = [  # (a, b, label)
    ("Atlanta", "Jacksonville", "100G"), ("Atlanta", "Boca Raton", "300G"),
    ("Jacksonville", "Miami", "200G"), ("Boca Raton", "Miami", ""),
    ("Boca Raton", "Fortaleza", "1.1 Tbps"), ("Fortaleza", "Sao Paulo", "1.2 Tbps"),
    ("Fortaleza", "Sangano", "100G"), ("Sao Paulo", "Porto Alegre", "100G"),
    ("Porto Alegre", "Buenos Aires", "100G"), ("Sao Paulo", "Buenos Aires", "400G"),
    ("Buenos Aires", "Santiago", "300G"), ("Miami", "San Juan", "100G"),
    ("San Juan", "Fortaleza", "100G"), ("Miami", "Panama City", "100G"),
    ("Panama City", "Santiago", "100G"), ("Fortaleza", "Sines", "200G"),
    ("Sangano", "Cape Town", "100G"),
]
# Approximate route of the Cape Town VLAN into Miami (AmLight / RNP).
CAPE_ROUTE = ["Cape Town", "Sangano", "Fortaleza", "Boca Raton", "Miami"]
SITE_FALLBACK = {"FIU": (25.76, -80.37), "CIEN": (45.35, -75.92)}


def _site_loc(fablib, site):
    try:
        lat, lon = fablib.get_resources().get_location_lat_long(site)
        return float(lat), float(lon)
    except Exception:
        return SITE_FALLBACK.get(site)


def path_map(fablib, legs: dict):
    """Interactive map (plotly) of the Cape Town <-> FABRIC Globus data path."""
    import plotly.graph_objects as go

    fig = go.Figure()
    for i, (a, b, cap) in enumerate(AMLIGHT_LINKS):
        (la1, lo1), (la2, lo2) = AMLIGHT_POPS[a], AMLIGHT_POPS[b]
        fig.add_trace(go.Scattergeo(lat=[la1, la2], lon=[lo1, lo2], mode="lines",
                                    line=dict(width=1.5, color="#b8c9a3"), hoverinfo="text",
                                    text=f"AmLight {a}-{b} {cap}", name="AmLight network",
                                    legendgroup="am", showlegend=i == 0))
    route = [AMLIGHT_POPS[p] for p in CAPE_ROUTE]
    fig.add_trace(go.Scattergeo(lat=[p[0] for p in route], lon=[p[1] for p in route], mode="lines",
                                line=dict(width=5, color=GREEN),
                                name="Globus DATA: Cape Town VLAN (L2, no routing)", hoverinfo="name"))
    cape = AMLIGHT_POPS["Cape Town"]
    fig.add_trace(go.Scattergeo(
        lat=[cape[0]], lon=[cape[1]], mode="markers+text", textposition="middle right",
        text=["<b>Cape Town DTN</b> (SANReN)<br>Globus Connect Personal"],
        marker=dict(size=20, symbol="star", color=PURPLE, line=dict(width=2, color="white")),
        hoverinfo="text", name="SANReN Cape Town DTN"))
    for site, leg in legs.items():
        loc = _site_loc(fablib, site)
        if not loc:
            continue
        if site != "FIU" and "FIU" in legs:          # other sites hang off Miami via FABRIC
            fiu = _site_loc(fablib, "FIU")
            fig.add_trace(go.Scattergeo(lat=[fiu[0], loc[0]], lon=[fiu[1], loc[1]], mode="lines",
                                        line=dict(width=3, color=GREEN, dash="dash"),
                                        name=f"FABRIC L2 to {site}", hoverinfo="name"))
        hover = (f"<b>{site} DTN</b> (FABRIC VM '{leg.get('node', 'dtn')}')<br>"
                 f"data {leg.get('data_addr')} on {leg.get('l2_fp')} VLAN {leg.get('l2_vlan')}<br>"
                 f"public {leg.get('public_ip') or '-'} / {leg.get('public_ip6') or '-'}")
        fig.add_trace(go.Scattergeo(
            lat=[loc[0]], lon=[loc[1]], mode="markers+text", textposition="top center",
            text=[f"<b>{site} DTN</b><br>GCS 5.4"], hovertext=hover, hoverinfo="text",
            marker=dict(size=16, color=BLUE, line=dict(width=2, color="white")), name=f"{site} DTN"))
    fig.add_annotation(
        x=0.01, y=0.04, xref="paper", yref="paper", showarrow=False, align="left",
        bgcolor="rgba(255,255,255,0.85)", bordercolor="#ccc",
        text=("<b>Control</b>: Globus cloud ↔ each DTN's public IP (TCP 443)<br>"
              "<b>Data</b>: Cape Town → DTN data NIC (TCP 50000-51000), on the green path"))
    fig.update_geos(projection_type="natural earth", showland=True, landcolor="#f2f3f4",
                    showocean=True, oceancolor="#eaf2f8", showcountries=True, countrycolor="#cfd8dc",
                    lataxis_range=[-45, 55], lonaxis_range=[-110, 40])
    fig.update_layout(title="Globus between Cape Town and FABRIC: control on the internet, data on the research VLAN",
                      height=560, margin=dict(l=0, r=0, t=50, b=0), legend=dict(orientation="h", y=-0.02))
    return fig


STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools", "demo_ui", "static")
# Route overlays in image pixels, the same paths the web UI draws (start, then (control, end) pairs:
# quadratic Bezier segments; control None = straight line).
MAP_ROUTES = {
    "amlight-map.jpg": dict(start=(982, 583), segs=[((965, 525), (937, 477)), ((790, 372), (606, 384)),
                                                    ((520, 230), (318, 162)), (None, (316, 180))],
                            pins=[(982, 583), (316, 180)], label=((690, 345), "Cape Town VLAN"),
                            title="On AmLight: Cape Town to Miami (route approximate)"),
    "fabric-map.jpg": dict(start=(1710, 895), segs=[((1450, 770), (1170, 806)), ((1185, 750), (1222, 694))],
                           pins=[(1222, 694), (1710, 895)], label=((1255, 650), "FABRIC DTN"),
                           title="Into FABRIC at FIU/AMPATH", src_w=1955),
}


def _bezier(start, segs, n=40):
    xs, ys = [start[0]], [start[1]]
    p0 = start
    for ctrl, end in segs:
        for i in range(1, n + 1):
            t = i / n
            if ctrl is None:
                x, y = p0[0] + (end[0] - p0[0]) * t, p0[1] + (end[1] - p0[1]) * t
            else:
                x = (1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * ctrl[0] + t ** 2 * end[0]
                y = (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * ctrl[1] + t ** 2 * end[1]
            xs.append(x), ys.append(y)
        p0 = end
    return xs, ys


def network_maps(site_label: str = "FIU"):
    """The AmLight and FABRIC network maps with the Cape Town data path highlighted."""
    import matplotlib.image as mpimg
    import matplotlib.patheffects as pe

    fig, axes = plt.subplots(1, 2, figsize=(15, 5.6), gridspec_kw={"width_ratios": [1.0, 1.45]})
    for ax, (name, r) in zip(axes, MAP_ROUTES.items()):
        img = mpimg.imread(os.path.join(STATIC, name))
        scale = img.shape[1] / r.get("src_w", img.shape[1])     # overlay coords are in source pixels
        ax.imshow(img)
        xs, ys = _bezier(r["start"], r["segs"])
        xs, ys = [x * scale for x in xs], [y * scale for y in ys]
        ax.plot(xs, ys, color="white", lw=9, alpha=.55, solid_capstyle="round")
        ax.plot(xs, ys, color="#39ff88", lw=4, ls=(0, (4, 2.5)), solid_capstyle="round")
        for px, py in r["pins"]:
            ax.plot(px * scale, py * scale, "o", ms=11, color="#39ff88", mec="white", mew=2)
        (lx, ly), text = r["label"]
        if text == "FABRIC DTN":
            text = f"FABRIC DTN · {site_label}"
        ax.text(lx * scale, ly * scale, text, color="white", fontsize=12, weight="bold",
                path_effects=[pe.withStroke(linewidth=3, foreground="black")])
        ax.set_title(r["title"], fontsize=12, loc="left")
        ax.axis("off")
    fig.tight_layout()
    return fig


class LivePathPlot:
    """Updates in place during a transfer: Gb/s per path over time, and bytes per path."""

    def __init__(self, title: str):
        from IPython.display import display

        self.title = title
        self.t0 = time.time()
        self.t, self.rates, self.last = [], {}, {}
        self.totals = {}
        self.fig, (self.ax1, self.ax2) = plt.subplots(1, 2, figsize=(13, 4),
                                                      gridspec_kw={"width_ratios": [3, 1]})
        plt.close(self.fig)                       # shown via the display handle only
        self.handle = display(self.fig, display_id=True)

    def update(self, totals: dict, status: str, task_bytes: int):
        """``totals``: path label -> cumulative bytes seen on the DTN's sockets."""
        now = time.time() - self.t0
        dt = (now - self.t[-1]) if self.t else None
        self.t.append(now)
        for label, b in totals.items():
            prev = self.last.get(label, (None, 0))[1]
            rate = (b - prev) * 8 / 1e9 / dt if dt else 0.0
            self.rates.setdefault(label, [0.0] * (len(self.t) - 1)).append(max(rate, 0.0))
            self.last[label] = (now, b)
        for label, series in self.rates.items():          # paths not seen this round
            if len(series) < len(self.t):
                series.append(0.0)
        self.totals = totals
        self._draw(status, task_bytes)

    def _draw(self, status, task_bytes):
        ax1, ax2 = self.ax1, self.ax2
        ax1.clear(), ax2.clear()
        for label, series in self.rates.items():
            ax1.plot(self.t, series, lw=2.5, color=PATH_COLORS.get(label, GREY), label=label)
            ax1.fill_between(self.t, series, color=PATH_COLORS.get(label, GREY), alpha=0.15)
        ax1.set_xlabel("seconds"), ax1.set_ylabel("Gb/s on the DTN's data sockets")
        ax1.set_title(f"{self.title}  -  {status}, {task_bytes / 1e9:,.2f} GB moved")
        if self.rates:
            ax1.legend(loc="upper left", frameon=False)
        labels = list(self.totals) or ["data NIC (Cape Town path)"]
        vals = [self.totals.get(l, 0) / 1e9 for l in labels]
        ax2.barh(labels, vals, color=[PATH_COLORS.get(l, GREY) for l in labels])
        ax2.set_xlabel("GB carried")
        ax2.set_title("by path")
        for ax in (ax1, ax2):
            ax.spines[["top", "right"]].set_visible(False)
        self.fig.tight_layout()
        self.handle.update(self.fig)


def path_summary(site: str, leg: dict, totals: dict, task: dict):
    """Result card: which path carried the bytes, and the effective rate."""
    data = totals.get("data NIC (Cape Town path)", 0)
    other = sum(v for k, v in totals.items() if k != "data NIC (Cape Town path)")
    ok = data > 0 and other == 0
    rate = (task.get("effective_bytes_per_second") or 0) * 8 / 1e9
    fig, ax = plt.subplots(figsize=(13, 1.6))
    ax.axis("off")
    color = GREEN if ok else (RED if data == 0 else ORANGE)
    head = ("ALL data on the Cape Town research path" if ok else
            "NO data caught on the research path" if data == 0 else "Data split across paths")
    ax.text(0.01, 0.62, f"{site}: {head}", fontsize=17, weight="bold", color=color, transform=ax.transAxes)
    ax.text(0.01, 0.15,
            f"{task['status']}  |  {task['bytes_transferred'] / 1e9:,.2f} GB  |  {rate:.2f} Gb/s effective  |  "
            f"data NIC {leg['data_ip']} carried {data / 1e9:,.2f} GB, public {leg.get('public_ip')} carried "
            f"{other / 1e9:,.2f} GB", fontsize=11, transform=ax.transAxes)
    return fig
