# netbox-oxidized-viewer

A NetBox plugin that surfaces [Oxidized](https://github.com/ytti/oxidized) configuration backups inside NetBox — with full history, side-by-side diffs, full-text search, and NetBox's native RBAC.

Oxidized has no authentication. This plugin gates config access behind NetBox's existing object permissions: if a user can view a device, they can see its config history.

## Features

- **Config History tab** injected into every device detail page — current config, last backup time, full commit timeline.
- **Diff viewer** — side-by-side and inline, Prism.js syntax highlighting, commit picker, download patch.
- **Full-text search** across all device configs via a Postgres GIN index. Sub-15 ms for selective terms at 1,000+ devices.
- **Raw config download** — current config or any historical commit as a plain-text file.
- **Commit notes** — attach a reason to any config change (by commit SHA), from the UI or the API. Oxidized's own commit messages are generic; these record *why* it changed. Stored in NetBox, so historical commits can be annotated too.
- **On-demand sync** (optional) — trigger an immediate Oxidized backup for a device from NetBox or the API, for a "change → resync → annotate" automation loop. Requires an Oxidized API URL on the source.
- **Backup scope** — limit which devices are Oxidized's responsibility by role, platform, or tag, so passive gear/servers/etc. are excluded from the exported node list *and* from the "never backed up" report.
- **Backup health** — the dashboard flags stale backups and lists active, in-scope devices that have never been backed up (the silent-failure case), in a collapsible panel.
- **REST API** — read a device's config, history, and diffs via NetBox tokens with the same object-level RBAC as the UI.
- **Inventory endpoint** — `GET /api/plugins/oxidized-viewer/inventory/` returns the device list in Oxidized's HTTP source format (NetBox platform as the driver name, primary IPv4 or any custom field as the address), so Oxidized can pull its node list directly from NetBox.
- **Read-only toward git** — the plugin never writes to the Oxidized git repo. (Commit notes live in NetBox; the optional sync only asks Oxidized to poll.)

## Requirements

- NetBox ≥ 4.3.0 (Python 3.10+; NetBox 4.5 itself requires Python ≥ 3.12)
- PostgreSQL (required for the FTS index — the `django.contrib.postgres` app must be in `INSTALLED_APPS`, which NetBox sets by default)
- The Oxidized bare git repository mounted read-only into the NetBox container, in a
  **flat** layout (one file per node at the top level — Oxidized's default; grouped
  `single_repo` subdirectory layouts are not matched)

## Installation

See [docs/install.md](docs/install.md) for the full step-by-step guide.

Quick path — install the package (inside the NetBox container or your custom image build):

```bash
# From PyPI (once a release is published):
pip install netbox-oxidized-viewer

# …or straight from GitHub today (no PyPI release required):
pip install git+https://github.com/CG-Djazairi/netbox-oxidized-viewer.git@main
```

For netbox-docker, add one of those lines to `local_requirements.txt` and rebuild.

Then add to `configuration.py` — set `git_repo_path` to the repo you mounted and
the source is created automatically on `migrate` (no UI step):

```python
PLUGINS = ['netbox_oxidized_viewer']

PLUGINS_CONFIG = {
    'netbox_oxidized_viewer': {
        'git_repo_path': '/opt/oxidized-git',  # auto-provisions the source
        # optional: 'node_name_source': 'name', 'api_url': 'http://oxidized:8888'
    },
}
```

Run migrations and collect the bundled static assets:

```bash
python manage.py migrate            # also auto-creates the source from the config above
python manage.py collectstatic --no-input   # vendored Prism.js syntax highlighter
python manage.py reindex_oxidized   # optional: build the search index now (else the scheduled job does)
```

Prefer to configure it by hand? Omit `git_repo_path` and add the source under
**Oxidized → Sources** in the UI instead — both paths work, and the UI stays
authoritative once a source exists.

## Architecture

```
[Oxidized] → writes configs → [Bare Git Repo]
                                     ↓  (read-only volume mount)
[NetBox Plugin] ← reads via Dulwich (pure-Python Git)
```

Config content is indexed into PostgreSQL (`ConfigSnapshot`) by a NetBox system background job (run via RQ) on a configurable interval. The live git repo is only read on device config/diff/download views — never on search.

The plugin reads the git repository directly (via Dulwich) rather than Oxidized's REST API: the API has no authentication and no usable fleet-wide search, and the history, diffs and Postgres search index are all built from the raw git objects. When NetBox and Oxidized run on separate hosts, sync the repo to the NetBox host with a GitOps pull or a network file share — see [docs/install.md](docs/install.md#production-when-oxidized-and-netbox-run-on-different-hosts).

## License

Apache 2.0
