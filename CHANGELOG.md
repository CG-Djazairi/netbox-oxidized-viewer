# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

