"""Shared FABRIC connection settings for the Cape Town Globus data-path weave.

``provision.py`` (the weave) and ``globus_transfer_demo.ipynb`` both import this
so they talk to FABRIC with the same token and project:

- Token: the user's default FABRIC token (``FABRIC_TOKEN_LOCATION`` / ``fabric_rc``).
  Set ``CRYOEM_TOKEN`` to use another file.
- Project: "CryoEM Without Borders" (override with ``CRYOEM_PROJECT_ID``).
"""

from __future__ import annotations

import json
import os

WEAVE_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(WEAVE_DIR, "results")
LEGS_FILE = os.path.join(RESULTS_DIR, "legs.json")

PROJECT_NAME = "CryoEM Without Borders"
PROJECT_ID = os.environ.get("CRYOEM_PROJECT_ID", "c93be0c1-696c-4c36-9dbd-03b14be196af")


def get_fablib():
    """FablibManager bound to the project, using the default token unless
    ``CRYOEM_TOKEN`` names another one."""
    from fabrictestbed_extensions.fablib.fablib import FablibManager

    token = os.environ.get("CRYOEM_TOKEN")
    if token:
        return FablibManager(project_id=PROJECT_ID, token_location=os.path.expanduser(token))
    return FablibManager(project_id=PROJECT_ID)


def load_legs() -> dict:
    """What the weave built (results/legs.json)."""
    if not os.path.exists(LEGS_FILE):
        raise RuntimeError(f"{LEGS_FILE} not found - run the weave first.")
    with open(LEGS_FILE) as fh:
        return json.load(fh)
