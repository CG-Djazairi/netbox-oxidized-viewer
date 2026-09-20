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

The plugin reads the **git repository** directly (with Dulwich), not Oxidized's REST API.
oxidized-web does expose the current config, the version list and diffs per node, but
it has no authentication and no usable fleet-wide search, and the plugin's history
timeline, diff viewer and Postgres search index are all built from the raw git objects.
So a local repo path is required today; an API transport is a possible future option.

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

The canonical path is `/api/plugins/oxidized-viewer/inventory/`; `source/` is an alias
kept for existing Oxidized configurations. Both, and the per-device endpoints, are listed
at the plugin's API root.

### Several Oxidized instances, one repository

When each security zone runs its own Oxidized, all of them can still write to the one
repository the plugin reads (each instance pushes to a shared git remote with Oxidized's
git-remote hook; the NetBox host mirrors it). What differs per instance is *which devices it
polls*: create one **Inventory** per instance under **Oxidized → Inventories**, give it a
scope (sites, roles, platforms, tags) and point that instance at the URL shown on its page:

```
https://<netbox>/api/plugins/oxidized-viewer/inventory/<slug>/
```

An inventory exports the devices that match its own scope **and** the source's backup
scope, so it can narrow the fleet but never widen it. A disabled inventory answers 404.
The token used by an instance still needs the view permission on devices, and a constrained
permission narrows the export further.

`ip` is the device's primary IPv4 by default. If your management addresses live
elsewhere, set **Inventory IP field** on the source (Oxidized → Sources → edit) to any
device attribute or custom field (`cf_<name>`); an object custom field referencing an IP
address exports the bare address. The `inventory_ip_field` key in `PLUGINS_CONFIG` only
seeds that value when the source is auto-created.

Devices whose field is empty are exported with `"ip": ""`, which makes Oxidized
fall back to DNS for them.

> **Permission required:** the token's user needs the `dcim.view_device`
> permission (a NetBox object permission with the *view* action on
> *DCIM → device*). The endpoint only exports devices that permission allows,
> so a constrained permission (e.g. a site filter) scopes the inventory too.

---

## 9. Backup health: let Oxidized report every run (recommended)

Oxidized commits only when a configuration **changes**. The age of a device's newest
commit therefore says when it last changed, not when it was last backed up: a switch
that is polled successfully every hour but never changes has an old commit. For real
health the plugin needs Oxidized to report the outcome of each run. Two ways:

**Exec hook (works everywhere, nothing to install).** Add to the Oxidized config:

```yaml
hooks:
  netbox_status:
    type: exec
    events: [node_success, node_fail]
    async: true
    timeout: 20
    cmd: '/usr/bin/curl -sk -m 15 -X POST -H "Authorization: Token <token>" --data-urlencode "event=$OX_EVENT" --data-urlencode "node=$OX_NODE_NAME" --data-urlencode "status=$OX_JOB_STATUS" --data-urlencode "err_type=$OX_ERR_TYPE" --data-urlencode "err_reason=$OX_ERR_REASON" https://<netbox>/api/plugins/oxidized-viewer/hook/'
```

The token's user needs the `netbox_oxidized_viewer.add_backupstatus` permission (object
type *Oxidized Config Viewer > backup status*, action *add*) and the view permission on
the devices. Oxidized runs hooks with a clean environment, so no proxy variable leaks in;
use the absolute path to curl for the same reason.

**nodes.json (when oxidized-web runs).** If the source has an API URL, the index job reads
`<api url>/nodes.json` on every run and records each node's last outcome. No hook needed.

With either in place the dashboard and the device card show, per device: *Up to date*
(latest run succeeded within `stale_after_hours`), *Failing* (latest run failed, with
Oxidized's reason), *Stale* (runs used to be reported and stopped) and *Never backed up*.
Without any report, a device whose config changed recently is *Up to date* and the others
are *Unverified*: unchanged or broken, the plugin cannot know.

## 10. (Optional) Commit notes and on-demand sync

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

Adding notes requires the `netbox_oxidized_viewer.add_configcommitnote` permission.
Triggering a sync requires the **change** permission on the device, since it makes
Oxidized act. Both also need what reading a configuration needs, see
[Permissions](#permissions).

To enable the **Sync now** button / `sync/` endpoint, set the source's **API URL**
to your Oxidized web endpoint (e.g. `http://oxidized:8888`). The plugin calls
`GET /node/next/<node>` to move the node to the head of Oxidized's poll queue.

> **Security:** Oxidized's REST API is **unauthenticated**. Only set the API URL if
> the Oxidized web port is network-restricted (firewall / internal network). Leaving
> it blank disables the sync feature entirely; history, diff, search, and notes all
> continue to work from git alone.

---

## Permissions

NetBox has no permission that covers a whole plugin: permissions are per object type.
Create them under **Admin → Permissions**, object types *Oxidized Config Viewer > …*.
Superusers bypass all of them, so test with a normal account.

| To… | Object type | Action |
|---|---|---|
| Read configurations: dashboard, search, the device's **Config History** tab and **Config Backup** card, diffs, downloads, the `devices/<id>/…` API | *config snapshot* | view |
| Add a commit note | *config commit note* | add (plus the row above) |
| Trigger a sync | *DCIM > device* | change (plus the first row) |
| Manage the source and the named inventories (**Oxidized → Admin** menu) | *oxidized source*, *oxidized inventory* | view / add / change / delete |
| Oxidized pulling its inventory (`inventory/`) | *DCIM > device* | view |
| Oxidized reporting its runs (`hook/`) | *backup status* | add (plus view on devices) |

Reading configurations always needs **both** *view* on *config snapshot* and *view* on the
device itself. Which devices a user sees is decided by the device permission: constrain
that one (by site, tenant, role, …) to limit a team to its own equipment. Constraints set
on the *config snapshot* permission are not evaluated, it is an on/off switch.

Without the *config snapshot* permission a user sees no **Oxidized → Configs** menu, no
**Config History** tab and no **Config Backup** card, and the pages and API endpoints
answer 403. The token Oxidized uses needs none of it.

> **Upgrading from 0.1.7 or earlier:** reading configurations used to need only *view* on
> the device. After the upgrade, grant *view* on *config snapshot* to every user, group
> and API token (automation pulling configs) that must keep that access.

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
