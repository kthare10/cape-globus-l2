"""Shared FABRIC connection settings for the Cape Town Globus data-path weave.

``provision.py`` (the weave) and ``globus_transfer_demo.ipynb`` both import this
so they talk to FABRIC with the same token and project:

Token and project both come from the default FABRIC config (``fabric_rc`` /
LoomAI's Configure view); the weave never overrides them. That project needs the
``Net.FacilityPort.<port>`` (or ``Net.AllFacilityPorts``) tags for the ports used.
"""

from __future__ import annotations

import base64
import json
import os

WEAVE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(WEAVE_DIR, "results")
LEGS_FILE = os.path.join(RESULTS_DIR, "legs.json")

def get_fablib():
    """FablibManager with the default FABRIC config: its token and its project."""
    from fabrictestbed_extensions.fablib.fablib import FablibManager

    return FablibManager()


def token_project(fablib) -> dict:
    """{'name', 'uuid', 'tags'} of the project in the token FABRIC checks (best effort)."""
    try:
        with open(os.path.expanduser(fablib.get_token_location())) as fh:
            tok = json.load(fh)["id_token"]
        part = tok.split(".")[1]
        part += "=" * (-len(part) % 4)
        projects = json.loads(base64.urlsafe_b64decode(part)).get("projects") or []
        return projects[0] if projects else {}
    except Exception:
        return {}


def load_legs() -> dict:
    """What the weave built (results/legs.json)."""
    if not os.path.exists(LEGS_FILE):
        raise RuntimeError(f"{LEGS_FILE} not found - run the weave first.")
    with open(LEGS_FILE) as fh:
        return json.load(fh)
