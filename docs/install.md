# Installation guide

## Prerequisites

- NetBox 4.5.0 or later running under Docker (or any deployment with PostgreSQL and Redis)
- Oxidized running and writing to a bare git repository
- The bare git repository accessible (as a read-only bind mount) from inside the NetBox container

---

## 1. Install the package

Inside the NetBox container, or in your custom image layer:

```bash
pip install netbox-oxidized-viewer
```

If you manage dependencies via a `local_requirements.txt` file (the netbox-docker pattern):

```
# local_requirements.txt
netbox-oxidized-viewer
```

Then rebuild or restart the container.

---

## 2. Mount the Oxidized git repository

The plugin reads configs from the bare git repo that Oxidized writes. Mount it read-only into the NetBox container.

**docker-compose.override.yml** (netbox-docker pattern):

```yaml
services:
  netbox: &netbox
    volumes:
      - /path/to/oxidized/git-output:/opt/oxidized-git:ro
  netbox-worker:
    volumes:
      - /path/to/oxidized/git-output:/opt/oxidized-git:ro
```

Replace `/path/to/oxidized/git-output` with the actual path on your host where Oxidized writes its bare repo. Mount it into the worker container too — the indexing task runs there.

> **Ownership note:** Oxidized runs as a non-root UID (commonly 30000). The plugin uses [Dulwich](https://www.dulwich.io/) (pure-Python Git) which is not affected by Git's `safe.directory` restriction, so no extra environment variables are needed.

### Production: when Oxidized and NetBox run on different hosts

The plugin needs **file-level access to the git repository** — not Oxidized's REST API. The
REST API can only return the *current* config; it cannot provide commit history, diffs, or let
Postgres index historical commits for full-text search. The whole point of the plugin (the
Config History timeline, the diff viewer, and the search index) depends on reading the raw git
objects, so a local repo path is a hard requirement.

In the lab this is just a bind mount. In production, where Oxidized and NetBox are usually
separate servers, get the repo onto the NetBox host with either:

1. **GitOps sync (recommended).** Configure Oxidized to push its backups to a central git remote
   (internal GitLab/Gitea/GitHub) via its built-in remote-output feature. On the NetBox host, run
   a scheduled `git pull` (cron/systemd timer) into a local clone, e.g. `/opt/oxidized-git`, and
   mount that clone read-only into the container exactly as above. This decouples NetBox from
   Oxidized's availability and gives you a single source of truth for backups.
2. **Network file share.** Export the Oxidized `git-output` directory over NFS/SMB and mount it on
   the NetBox host, then bind-mount it into the container. Simpler, but couples NetBox uptime to
   the file share.

Either way the plugin's view of the data is identical — it always reads a local git repo.

---

## 3. Enable the plugin in `configuration.py`

```python
PLUGINS = [
    'netbox_oxidized_viewer',
]

PLUGINS_CONFIG = {
    'netbox_oxidized_viewer': {},
}
```

---

## 4. Run migrations

```bash
docker exec netbox-docker-netbox-1 python manage.py migrate netbox_oxidized_viewer
```

This creates three tables and a GIN index on the `search_vector` column. The GIN index is built with `CREATE INDEX CONCURRENTLY` (migration uses `atomic=False`) so it does not lock the table on production deployments.

Then collect the plugin's static assets (the bundled Prism.js syntax highlighter):

```bash
docker exec netbox-docker-netbox-1 python manage.py collectstatic --no-input
```

---

## 5. Configure an Oxidized Source

1. Open NetBox → **Oxidized → Sources → Add**.
2. Fill in:
   - **Name** — a label for this source (e.g. `Lab`).
   - **Git repo path** — the path inside the NetBox container (e.g. `/opt/oxidized-git`).
   - **Node name source** — how device names map to filenames in the git repo. Default is `name` (NetBox device name = Oxidized node name = git filename). Other valid values: `serial`, `asset_tag`, `primary_ip4`, or any custom field prefixed with `cf_` (e.g. `cf_oxidized_name`).

---

## 6. Build the search index

Run the indexer once manually to populate the full-text search index immediately
(otherwise the scheduled job below builds it on its next run):

```bash
docker exec netbox-docker-netbox-1 python manage.py reindex_oxidized
```

To reindex a single source by PK:

```bash
docker exec netbox-docker-netbox-1 python manage.py reindex_oxidized --source 1
```

---

## 7. Automatic re-indexing

The plugin registers a NetBox **system background job** (`ConfigSnapshotIndexJob`)
that re-indexes configs on a fixed interval. NetBox runs background jobs with RQ,
so the only requirement is that an RQ worker is running — which it already is in a
standard NetBox deployment:

```bash
docker exec netbox-docker-netbox-1 python manage.py rqworker
```

No `configuration.py` scheduling is required. To change the interval (default 60
minutes), set it in `PLUGINS_CONFIG`:

```python
PLUGINS_CONFIG = {
    'netbox_oxidized_viewer': {
        'index_interval_minutes': 15,
    },
}
```

You can confirm the job is registered under **Admin → System → Background Tasks**
(or *Jobs*) in the NetBox UI after the worker has started.

---

## 8. (Optional) Configure Oxidized to pull its device list from NetBox

Instead of maintaining a static `router.db`, Oxidized can call the plugin's inventory endpoint.

**Oxidized `config` file:**

```yaml
source:
  default: http
  http:
    url: http://<netbox-host>/api/plugins/oxidized-viewer/source/
    map:
      name: name
      model: model
      ip: ip
    headers:
      Authorization: Token <your-netbox-api-token>
```

The endpoint returns active devices in the format `[{"name": "...", "model": "...", "ip": "..."}]`.

> **Permission required:** the token's user needs the `dcim.view_device`
> permission (a NetBox object permission with the *view* action on
> *DCIM → device*). The endpoint only exports devices that permission allows,
> so a constrained permission (e.g. a site filter) scopes the inventory too.

---

## Verifying the installation

```bash
# Check migrations applied
docker exec netbox-docker-netbox-1 python manage.py showmigrations netbox_oxidized_viewer

# Check snapshots were created
docker exec netbox-docker-netbox-1 python manage.py shell -c \
  "from netbox_oxidized_viewer.models import ConfigSnapshot; print(ConfigSnapshot.objects.count(), 'snapshots')"
```

Then open any device in NetBox — you should see a **Config History** tab. Open **Oxidized → Search Configs** to verify full-text search works.
