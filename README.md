# netbox-oxidized-viewer

A NetBox plugin that surfaces [Oxidized](https://github.com/ytti/oxidized) configuration backups inside NetBox — with full history, side-by-side diffs, full-text search, and NetBox's native RBAC.

Oxidized has no authentication. This plugin gates config access behind NetBox's existing object permissions: if a user can view a device, they can see its config history.

## Features

- **Config History tab** injected into every device detail page — current config, last backup time, full commit timeline.
- **Diff viewer** — side-by-side and inline, Prism.js syntax highlighting, commit picker, download patch.
- **Full-text search** across all device configs via a Postgres GIN index. Sub-15 ms for selective terms at 1,000+ devices.
- **Raw config download** — current config or any historical commit as a plain-text file.
- **Inventory endpoint** — `GET /api/plugins/oxidized-viewer/source/` returns the device list in Oxidized's HTTP source format, so Oxidized can pull its node list directly from NetBox.
- **Read-only** — never writes to devices or to the git repo.

## Requirements

- NetBox ≥ 4.5.0
- PostgreSQL (required for the FTS index — the `django.contrib.postgres` app must be in `INSTALLED_APPS`, which NetBox sets by default)
- The Oxidized bare git repository mounted read-only into the NetBox container

## Installation

See [docs/install.md](docs/install.md) for the full step-by-step guide.

Quick path:

```bash
# Inside the NetBox container, or in your custom image build
pip install netbox-oxidized-viewer
```

Then add to `configuration.py`:

```python
PLUGINS = ['netbox_oxidized_viewer']

PLUGINS_CONFIG = {
    'netbox_oxidized_viewer': {},
}
```

Run migrations and build the search index:

```bash
python manage.py migrate netbox_oxidized_viewer
python manage.py reindex_oxidized
```

## Architecture

```
[Oxidized] → writes configs → [Bare Git Repo]
                                     ↓  (read-only volume mount)
[NetBox Plugin] ← reads via Dulwich (pure-Python Git)
```

Config content is indexed into PostgreSQL (`ConfigSnapshot`) by a NetBox system background job (run via RQ) on a configurable interval. The live git repo is only read on device config/diff/download views — never on search.

The plugin reads the git repository directly (via Dulwich) rather than Oxidized's REST API, because the API cannot provide commit history, diffs, or a searchable index of past commits. When NetBox and Oxidized run on separate hosts, sync the repo to the NetBox host with a GitOps pull or a network file share — see [docs/install.md](docs/install.md#production-when-oxidized-and-netbox-run-on-different-hosts).

## License

Apache 2.0
