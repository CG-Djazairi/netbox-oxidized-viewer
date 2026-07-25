# ADR 0004 — Replace live git grep with Postgres full-text search

**Status:** Accepted  
**Date:** 2026-05-04

## Context

The v0.1.0 search feature calls `GitBackend.search_content()` on every request.
This walks the entire HEAD tree in dulwich, loading every config file into memory
and doing a Python-level substring scan.  Measured scaling (query=`'interface'`,
~50 KB files, 5 cold runs each, host machine):

| N devices | avg latency | peak RAM |
|----------:|------------:|---------:|
|        10 |       148ms |   1.8 MB |
|       100 |     1,567ms |  17.2 MB |
|       500 |     8,062ms |  85.7 MB |
|     1,000 |    16,340ms | 171.3 MB |

Scaling is linear at ~16 ms/device.  The 200 ms target is blown at just 13
devices.  At 1,000 devices the request takes 16 seconds and loads 170 MB of raw
config into the Python process.

## Decision

Introduce a `ConfigSnapshot` model (one row per device) with a `SearchVectorField`
maintained by a `post_save` signal.  A NetBox system background job
(`update_config_snapshots`, run via RQ — see `jobs.py`) refreshes the snapshot
whenever the latest commit SHA changes.  Search queries use `SearchQuery` +
`SearchRank` + `SearchHeadline` against the GIN-indexed vector — all handled
inside PostgreSQL.

> **Update (post-0.1.0):** the original `GitBackend.search_content()` live-grep
> fallback and the "index not yet built" banner have been removed. Search now
> depends solely on the FTS index; run `manage.py reindex_oxidized` (or let the
> scheduled job run) to populate it. Background scheduling was also ported from
> Celery to NetBox's native RQ `JobRunner`, since NetBox does not ship Celery.

> **Update (2026-06-11):** `ConfigSnapshot` now also denormalizes
> `commit_timestamp` and `commit_subject` (migration 0006), populated by the
> same indexing job. The dashboard is served entirely from this table — its
> previous implementation walked git history per device per cold load,
> O(devices × history). The snapshot table is therefore the general pattern:
> Postgres answers every list/search query; git is only opened for file
> content and diffs. Headlines are emitted with sentinel delimiters and
> escaped server-side before `<mark>` insertion (stored-XSS fix).

> **Update (2026-07):** the `post_save` signal that recomputed `search_vector`
> was replaced by a Postgres **STORED generated column** (`GeneratedField`,
> migration 0007). Postgres now maintains the tsvector on every write, so the
> index cannot drift even for write paths that bypass `save()` (`bulk_create`,
> queryset `.update(content=...)`) — the class of staleness bug the signal
> could not cover. The indexing job's per-row write is also halved (one INSERT
> instead of INSERT + signal UPDATE). `config='simple'` is unchanged.

## Why pagination is essential

On the first implementation, `SearchHeadline` was applied to every row returned by
the GIN index.  For a selective term (matches 1 device), this is fine.  For a
broad term like `interface` (matches every device), the headline generation is
O(matches × content_size) — the same order as git-grep.

Measured impact of running `SearchHeadline` without pagination at 1,000 devices:
7,388ms avg, 80.9 MB peak — only 2× faster than git-grep.

**Fix:** count total matches cheaply with a `COUNT(*)` (index only), then apply
`SearchHeadline` only to the current page of ranked rows (`SEARCH_PAGE_SIZE`,
currently 25 — see `ConfigSearchView`).  Memory becomes constant at ~4 MB
regardless of N.

## Benchmark results (measured 2026-05-04, inside `netbox-docker-netbox-1`)

### Scenario A — selective term (unique loopback IP, 1 match)

| N devices | avg (ms) | vs. git-grep |
|----------:|---------:|-------------:|
|        10 |      9.5 |      16× faster |
|       100 |     10.1 |     155× faster |
|       500 |     13.4 |     602× faster |
|     1,000 |     13.8 | **1,183× faster** |

Scaling: **O(log N)**.  Memory constant at 0.1 MB.

### Scenario B — common term `'interface'`, paginated to 50 (production)

| N devices | avg (ms) | peak RAM | vs. git-grep |
|----------:|---------:|---------:|-------------:|
|        10 |       77 |   0.8 MB |       2× faster |
|       100 |      524 |   4.0 MB |       3× faster |
|       500 |    1,902 |   4.0 MB |       4× faster |
|     1,000 |    3,726 |   4.1 MB |     **4× faster** |

Memory capped at 4 MB.  Latency is still O(N matches) because `SearchRank` must
visit every matched row to identify the top 50.

Full measurement scripts and raw numbers live outside the published package
(developer working notes); the summary tables above are the reproducible result.

## Alternatives considered

**Elasticsearch / OpenSearch** — purpose-built for full-text search, excellent
ranking.  Rejected: adds a new stateful service dependency (container, storage,
Java heap), complicates the lab setup, and is completely disproportionate for
device counts in the hundreds or low thousands.

**SQLite FTS5** — not applicable; the project runs on PostgreSQL.  NetBox does
not support SQLite.

**In-memory trie/inverted index built on startup** — would give similar latency,
but the index is lost on every restart, does not survive worker restarts, and
cannot be shared across multiple Celery workers or NetBox processes.

**Redis full-text search (RediSearch)** — NetBox already uses Redis, but
RediSearch is a paid module not included in the stock `redis:latest` image.
Avoids the dependency rule.

**Postgres FTS (chosen)** — PostgreSQL is already the authoritative database.
`SearchVectorField` + a GIN index is a standard Django feature with no additional
service dependency.  Query cost is O(log N) in the index for selective terms.
`SearchHeadline` generates context snippets natively — better than the 2-line
window the Python code produced.

## Search vector configuration: `'simple'` not `'english'`

The `config='simple'` argument is used for both indexing and querying.

Network device configs are identifier-heavy: interface names (`ethernet-1/1`),
protocol keywords (`bgp`, `ospf`), and admin tokens (`admin-state`, `no shutdown`).
The English stemmer would cause two problems:

1. **False matches**: `routing` → `rout`, which matches `route` and `router`.
2. **Lost stop words**: `no` is an English stop word and would be dropped, making
   `no shutdown` unsearchable.

`'simple'` performs only lowercasing and whitespace tokenisation — no stemming, no
stop word removal — which is exactly right for the search domain.

## Consequences

- **Selective searches**: O(log N), sub-15ms at 1,000 devices.
- **Broad searches**: O(N matches) for ranking; headline computed only for the
  current page (`SEARCH_PAGE_SIZE`, currently 25 rows).
  Memory constant at ~4 MB regardless of N; 4× faster than git-grep at 1,000.
- Indexing runs as a NetBox system job (`ConfigSnapshotIndexJob`) on a
  configurable interval (`index_interval_minutes`, default 60) via the RQ worker.
  Also triggerable via `manage.py reindex_oxidized`.
- Until the first index build completes, search returns no results (run the
  management command once after install for immediate results).
- Migration `0003_configsnapshot` uses `atomic=False` + `AddIndexConcurrently`
  so the GIN index is built without an exclusive table lock on production.
