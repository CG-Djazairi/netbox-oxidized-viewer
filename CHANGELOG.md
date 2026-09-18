# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.7] - 2026-09-18

### Fixed
- A device that is backed up successfully but whose configuration never changes
  was flagged **stale**: Oxidized commits only on change, and staleness was
  computed from the age of the newest commit. Health now comes from the outcome
  of Oxidized's runs; without run reports such a device is *unverified*, never
  stale.

### Added
- `BackupStatus` per device (migration 0004), fed by Oxidized through
  `POST /api/plugins/oxidized-viewer/hook/` (exec hook on `node_success` /
  `node_fail`, permission `add_backupstatus`) or, when the source has an API URL,
  pulled from `nodes.json` by the index job.
- Dashboard and device card: *Up to date*, *Failing* (with Oxidized's error),
  *Stale* (runs stopped), *Unverified*, *Never backed up* (with the last reported
  failure when there is one); "last backup run" and "last change" are shown
  separately.

## [0.1.6] - 2026-09-18

### Added
- **Named inventories** (Oxidized → Inventories): one endpoint per Oxidized
  instance at `/api/plugins/oxidized-viewer/inventory/<slug>/`, scoped by sites,
  roles, platforms and/or tags and always within the source's backup scope. The
  inventory page shows the URL to paste into that instance's Oxidized config and
  a preview of the exported devices. Also exposed at `/api/.../inventories/`
  and listed at the API root. This is the intended way to run several Oxidized
  instances (one per zone) against one shared repository.

## [0.1.5] - 2026-09-17

### Fixed
- **Reindex now** on the source page failed with "Jobs cannot be assigned to this
  object type": `OxidizedSource` now carries NetBox's jobs feature, the job is
  attached to the source (new *Jobs* tab on the source page) and indexes that
  source.
- Snapshots of devices whose file disappeared from the repository (or that left
  the backup scope) are removed on the next index run instead of showing a
  healthy backup forever.
- Malformed commit SHAs in URLs now 404 at routing instead of reaching git or the
  database.
- The on-demand sync client no longer follows redirects and only accepts http(s)
  API URLs.

### Added
- **Inventory IP field** on the source (form, page, REST) — which device
  attribute the inventory exports as `ip`. Migration 0002 copies the previous
  `PLUGINS_CONFIG` value onto existing sources; the setting now only seeds an
  auto-created source.
- `/api/plugins/oxidized-viewer/inventory/` as the canonical inventory path
  (`source/` kept as an alias), and an API root that lists every plugin endpoint.

### Changed
- Triggering a sync (UI button and API) requires the **change** permission on the
  device instead of view only.
- Config bodies and diffs are cached for 5 minutes instead of 24 hours; they are
  plaintext configuration living in a shared Redis.
- Documentation no longer claims that Oxidized's REST API cannot serve history
  and diffs; the git requirement is explained by search and authentication instead.

## [0.1.4] - 2026-09-17

### Added
- Line numbers in the Config History view (Prism line-numbers plugin, vendored),
  and `#L<n>` anchors that highlight and scroll to a line.
- Search results now list the matching lines of each device with their line
  numbers, each linking to that line of the configuration, instead of a text
  snippet.
- Prefix matching in search: every term matches the start of an indexed token,
  so `10.10.10` finds `10.10.10.9` and `10.10.10.10`, and `krd` finds `1-krd-wa`.
  Terms are whitelisted before they reach the raw tsquery.

### Changed
- Saving an Oxidized Source always returns to the source's own page, also when
  the edit was opened from the list.

## [0.1.3] - 2026-09-16

### Added
- `inventory_ip_field` plugin setting: which device attribute the inventory
  endpoint exports as the Oxidized `ip` (default `primary_ip4`). Accepts any
  device attribute or a `cf_<name>` custom field; an object custom field that
  references an IP address exports the bare address.

### Changed
- `cf_<name>` expressions (node name, group, ip) now resolve through NetBox's
  custom-field deserializer, so object custom fields yield the object instead
  of its raw primary key.

## [0.1.2] - 2026-09-15

### Fixed
- Saving an Oxidized Source from the UI failed with `SerializerNotFound`: NetBox
  serializes every change-logged object with its REST API serializer, and the
  plugin shipped none for `OxidizedSource`. The serializer now exists.

### Added
- REST endpoint `/api/plugins/oxidized-viewer/sources/` (list/retrieve/create/
  update/delete, standard NetBox model endpoint) backed by the new serializer.

## [0.1.1] - 2026-09-15

### Changed
- Minimum supported NetBox lowered from 4.5.0 to **4.3.0** (`requires-python` >= 3.10 to
  match). The initial migration now depends on the last dcim/extras migrations of 4.3
  (`0207` / `0128`) and no longer references `netbox.models.deletion`, which only exists
  from 4.5. Tested on 4.3.1 and 4.5.0.

## [0.1.0] - 2026-07-25
- Initial release: Config History tab, side-by-side diff viewer, Postgres FTS search,
  raw config/diff downloads, and the Oxidized HTTP-source inventory endpoint.

### Added
- **Zero-touch install** — set `git_repo_path` (and optionally `node_name_source` /
  `api_url`) in `PLUGINS_CONFIG` and the `OxidizedSource` is auto-created on
  `migrate`, removing the manual UI step. Opt-in and create-if-absent: leaving it
  unset keeps the UI workflow, and once a source exists the UI stays authoritative.
- **Backup scope** — restrict which devices Oxidized should back up, by device
  **role**, **platform**, and/or **tag** on the source. Out-of-scope devices
  (passive gear, servers, etc.) are dropped from the exported inventory (so
  Oxidized never polls them), from the indexer, and from the dashboard's
  "never backed up" list. Empty scope = all active devices (unchanged behaviour).
  The dashboard's never-backed-up panel is now collapsible.
- **Commit notes** — attach a free-text reason to any config commit (by SHA),
  shown in the diff view and counted on the history tab. Stored in NetBox, never
  written into the git repo, so the plugin stays read-only and notes work on
  historical commits too. Addable via the UI or `POST /api/.../devices/<pk>/commits/<sha>/note/`.
- **On-demand "Sync now"** — optionally trigger an immediate Oxidized backup for a
  device (UI button + `POST /api/.../devices/<pk>/sync/`), enabling the automation
  loop: make a change → resync → annotate the new commit. Requires the source's
  optional `api_url`; Oxidized's API is unauthenticated, so network-restrict it.
- **Backup health on the dashboard** — devices whose newest backup is older than
  `stale_after_hours` (default 26h) are flagged *stale*, and active devices with a
  resolvable node name but no snapshot are listed as *never backed up*. Summary
  counters (total / ok / stale / missing).
- **Device "Backup Status" card** on the core device page (`PluginTemplateExtension`)
  showing last-backup time, short SHA, subject, and a freshness badge — served from
  the denormalized snapshot columns, no git access.
- **Read-only REST API** for a device's config content, commit history, and diff,
  gated by the same object-level RBAC as the UI.
- **Reindex-now** action on the source page, enqueued through NetBox's Jobs UI.
- Oxidized inventory endpoint now exports the correct **driver name** from the NetBox
  platform (with an optional `platform_model_map` override) and an optional `group`
  field (`inventory_group_field`), instead of the hardware model string.
- Form-level validation of `git_repo_path` — a bad path is rejected at save time.
- Test coverage for search-view RBAC, the cache layer (key isolation, negative-result
  caching), empty/non-UTF-8 repositories, the single-walk indexer, and the inventory
  driver mapping. CI, ruff config, and a `dev` extra.

### Changed
- `ConfigSnapshot.search_vector` is now a Postgres **STORED generated column**
  (`GeneratedField`, migration 0007) instead of being maintained by a `post_save`
  signal. The FTS index can no longer drift from content on write paths that bypass
  `save()` (`bulk_create`, queryset `.update()`), and the indexing job does one write
  per row instead of two.
- The indexing job walks the git history **once per source** (O(history)) instead of
  once per device (O(devices × history)).
- Single-`OxidizedSource` invariant is now enforced by a database constraint, not only
  by form-level `clean()`.
- `requires-python` corrected to `>=3.12` (NetBox 4.5 itself requires 3.12+).
- `dulwich` pinned to `>=0.21,<2.0`.

### Fixed
- Negative results (`get_latest_commit` → `None`, i.e. a device with no backups) are
  now cached instead of triggering a full-history re-walk on every request.
- Dashboard ordering no longer floats rows with a NULL `commit_timestamp` to the top.
- Download responses build the `Content-Disposition` filename via
  `content_disposition_header`, so device-controlled names can't break the header.
- `test_git_backend.py` is now collected by the Django test runner (was pytest-only,
  silently skipped); the vacuous signal-loop test was replaced with a behavioral one.

