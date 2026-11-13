"""Shared FABRIC connection settings for the Cape Town Globus data-path weave.

``provision.py`` (the weave) and ``globus_transfer_demo.ipynb`` both import this
so they talk to FABRIC with the same token and project:

- Token: the user's default FABRIC token (``FABRIC_TOKEN_LOCATION`` / ``fabric_rc``).
  Set ``CRYOEM_TOKEN`` to use another file.
- Project: the one the token / FABRIC config is set to (LoomAI's selected project).
  Set ``CRYOEM_PROJECT_ID`` to force another; that project needs the
  ``Net.FacilityPort.<port>`` (or ``Net.AllFacilityPorts``) tags for the ports used.
"""

from __future__ import annotations

import base64
import json
import os

WEAVE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(WEAVE_DIR, "results")
LEGS_FILE = os.path.join(RESULTS_DIR, "legs.json")

PROJECT_ID = os.environ.get("CRYOEM_PROJECT_ID") or None


def get_fablib():
    """FablibManager on the configured project (or ``CRYOEM_PROJECT_ID``), using the
    default token unless ``CRYOEM_TOKEN`` names another one."""
    from fabrictestbed_extensions.fablib.fablib import FablibManager

    kw = {"project_id": PROJECT_ID} if PROJECT_ID else {}
    token = os.environ.get("CRYOEM_TOKEN")
    if token:
        kw["token_location"] = os.path.expanduser(token)
    return FablibManager(**kw)


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
