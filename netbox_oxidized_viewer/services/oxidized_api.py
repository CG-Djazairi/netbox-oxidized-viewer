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


def _base(api_url: str) -> str:
    if not api_url:
        raise OxidizedAPIError('No Oxidized API URL is configured on the source.')
    if not api_url.lower().startswith(('http://', 'https://')):
        raise OxidizedAPIError('The Oxidized API URL must start with http:// or https://.')
    return api_url.rstrip('/')


def fetch_nodes(api_url: str, timeout: int = 15) -> list:
    """
    GET /nodes.json: every node Oxidized knows, with the outcome of its last run
    ({'name': ..., 'last': {'end': '2026-01-01 10:00:00 UTC', 'status': 'success'}}).
    """
    url = f'{_base(api_url)}/nodes.json'
    try:
        resp = requests.get(url, timeout=timeout, allow_redirects=False)
        resp.raise_for_status()
        nodes = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise OxidizedAPIError(f'Oxidized API request failed: {exc}') from exc
    if not isinstance(nodes, list):
        raise OxidizedAPIError('Unexpected nodes.json payload from Oxidized.')
    return nodes


def trigger_backup(api_url: str, node: str, timeout: int = 10) -> str:
    """
    Ask Oxidized to back up `node` now via GET /node/next/<node> (which moves the
    node to the head of the poll queue). Returns Oxidized's response body.

    Raises OxidizedAPIError on any connection/HTTP failure so callers can surface
    a friendly message instead of a 500.
    """
    url = f'{_base(api_url)}/node/next/{quote(node, safe="")}'
    try:
        # No redirects: NetBox must only ever talk to the configured host.
        resp = requests.get(url, timeout=timeout, allow_redirects=False)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise OxidizedAPIError(f'Oxidized API request failed: {exc}') from exc
    return resp.text
