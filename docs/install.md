# Installation guide

## Prerequisites

- NetBox 4.3.0 or later, under Docker or a classic install (any deployment with PostgreSQL and Redis)
- Oxidized running and writing to a bare git repository
- The bare git repository accessible (as a read-only bind mount) from inside the NetBox container
- A **flat** repository layout — one config file per node at the top level of the
  repo, which is Oxidized's default. Grouped layouts that write configs into
  subdirectories (`group/hostname`, produced by `single_repo: true` with groups) are
  **not** matched: the device tab shows "No configuration backups found" and the
  indexer skips them. If you use groups, drop `single_repo` so each group gets its
  own flat repo, and point a source at that repo.

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
    'netbox_oxidized_viewer': {
        # Optional zero-touch setup: set the mounted repo path here and the
        # source is auto-created on `migrate` — skip the UI step in section 5.
        # 'git_repo_path': '/opt/oxidized-git',
        # 'node_name_source': 'name',        # optional
        # 'api_url': 'http://oxidized:8888',  # optional (on-demand sync)
    },
}
```

> **Auto-provisioning is opt-in and non-destructive.** It only runs when
> `git_repo_path` is set, and only creates a source if none exists — once a
> source exists (from settings *or* the UI), the UI is authoritative and these
> settings never overwrite it. Leave `git_repo_path` unset to manage the source
> entirely in the UI (section 5).

---

## 4. Run migrations

```bash
docker exec netbox-docker-netbox-1 python manage.py migrate netbox_oxidized_viewer
```

This creates the plugin's two tables (`OxidizedSource` and `ConfigSnapshot`) and a GIN index on the `search_vector` column. `ConfigSnapshot.search_vector` is a Postgres **STORED generated column** (`to_tsvector('simple', content)`), so the full-text index is maintained by the database itself and can never drift from the config content.

Then collect the plugin's static assets (the bundled Prism.js syntax highlighter):

```bash
docker exec netbox-docker-netbox-1 python manage.py collectstatic --no-input
```

---

## 5. Configure an Oxidized Source

> **Skip this section** if you set `git_repo_path` in `PLUGINS_CONFIG` (section 3) —
> the source was already auto-created by `migrate`. You can still open it in the
> UI to adjust the node-name mapping, backup scope, or API URL.

1. Open NetBox → **Oxidized → Sources → Add**.
2. Fill in:
   - **Name** — a label for this source (e.g. `Lab`).
   - **Git repo path** — the path inside the NetBox container (e.g. `/opt/oxidized-git`).
   - **Node name source** — how device names map to filenames in the git repo. Default is `name` (NetBox device name = Oxidized node name = git filename). Other valid values: `serial`, `asset_tag`, `primary_ip4`, or any custom field prefixed with `cf_` (e.g. `cf_oxidized_name`).
   - **Backup scope** (optional) — restrict which devices Oxidized should back up by **device role**, **platform**, and/or **tag**. A device is in scope only if it matches every filter you set (an empty filter means "any"). Leave all three blank to include every active device. Use this to keep passive gear, servers, VMs, etc. out of the exported inventory (so Oxidized never polls them) and off the dashboard's "never backed up" list.

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

## 9. (Optional) Commit notes and on-demand sync

Oxidized's auto-generated commit messages are generic. This plugin lets you attach
a **note** to any commit explaining *why* the config changed. Notes are stored in
NetBox (never written into the git repo, so the plugin stays read-only and even
historical commits can be annotated) and appear on the diff view.

- **In the UI:** open a device → **Config History** → open a commit's diff → *Add note*.
- **Via the API** (for automation): after a change, resync the device and annotate
  the resulting commit.

```bash
# 1. Trigger an immediate backup (needs the source's API URL, see below)
curl -s -X POST -H "Authorization: Token $TOKEN" \
  https://netbox.example.com/api/plugins/oxidized-viewer/devices/42/sync/

# 2. …once Oxidized has committed the change, attach the reason to the new SHA
curl -s -X POST -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  -d '{"message": "CHG-4211: raise uplink MTU to 9216"}' \
  https://netbox.example.com/api/plugins/oxidized-viewer/devices/42/commits/<sha>/note/
```

Adding notes requires the `netbox_oxidized_viewer.add_configcommitnote` permission
(plus view permission on the device).

To enable the **Sync now** button / `sync/` endpoint, set the source's **API URL**
to your Oxidized web endpoint (e.g. `http://oxidized:8888`). The plugin calls
`GET /node/next/<node>` to move the node to the head of Oxidized's poll queue.

> **Security:** Oxidized's REST API is **unauthenticated**. Only set the API URL if
> the Oxidized web port is network-restricted (firewall / internal network). Leaving
> it blank disables the sync feature entirely; history, diff, search, and notes all
> continue to work from git alone.

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
