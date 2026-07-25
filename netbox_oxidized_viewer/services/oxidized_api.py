"""
Thin client for Oxidized's REST API — used *only* to trigger an on-demand
backup of a node. The plugin still reads all history from git; this never
replaces the git repository as the source of truth.

Oxidized's API is unauthenticated, so the configured api_url must be
network-restricted (see OxidizedSource.api_url help text).
"""

from urllib.parse import quote

import requests


class OxidizedAPIError(Exception):
    """Raised when the Oxidized API cannot be reached or returns an error."""


def trigger_backup(api_url: str, node: str, timeout: int = 10) -> str:
    """
    Ask Oxidized to back up `node` now via GET /node/next/<node> (which moves the
    node to the head of the poll queue). Returns Oxidized's response body.

    Raises OxidizedAPIError on any connection/HTTP failure so callers can surface
    a friendly message instead of a 500.
    """
    if not api_url:
        raise OxidizedAPIError("No Oxidized API URL is configured on the source.")

    url = f"{api_url.rstrip('/')}/node/next/{quote(node, safe='')}"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise OxidizedAPIError(f"Oxidized API request failed: {exc}") from exc
    return resp.text
