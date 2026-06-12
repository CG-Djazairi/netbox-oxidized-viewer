# ADR 0003: Git Backend — Dulwich over GitPython

## Status

Accepted

## Context

The plugin reads Oxidized configuration backups from a bare Git repository. Initially, GitPython was assumed as the library for this task.
However, GitPython shells out to the system's `git` binary for its operations (`git log`, `git diff`, etc.). The official `netbox-docker` image does not include a `git` binary. 

To use GitPython, users would need to install `git` into the container, which typically requires building a custom Docker image extending the official image or modifying the container entrypoint. This significantly increases the installation complexity for a NetBox plugin.

An alternative is `dulwich`, a pure-Python implementation of the Git file formats and protocols.

## Decision

We will use **dulwich** instead of GitPython for accessing the Oxidized Git repository.

## Rationale

- **No OS Dependencies**: `dulwich` is a pure-Python library. It can be installed entirely via `local_requirements.txt` alongside the plugin.
- **Simplified Deployment**: End-users will not need to build custom Docker images or modify container entrypoints to install the `git` binary.
- **Sufficient Capabilities**: The plugin only requires read-only access to a bare repository to fetch commit history, file contents, and diffs. `dulwich` provides sufficient APIs to perform these read operations directly on the Git object database.
- **Solves the "Dubious Ownership" Error**: The Oxidized bare repo is owned by a different UID (the Oxidized container user, commonly 30000) than the NetBox process. Git ≥ 2.35.2 refuses to operate on such a repo unless its path is whitelisted in `safe.directory`. Because `dulwich` operates natively on the Git object database format (rather than shelling out to the `git` C binary), it simply does not enforce these ownership checks — so no `safe.directory` configuration or environment variables are required at all.

## Consequences

- We avoid the deployment complexity of adding OS packages to the `netbox-docker` image.
- **Diff API Ergonomics**: `dulwich`'s API is significantly lower-level than GitPython's. While GitPython can produce a unified diff in a single method call (`repo.git.diff(...)`), `dulwich` requires us to manually walk the tree, locate the blobs for a given file at two different commits, read their content, and then run them through Python's `difflib` (or `dulwich.patch.write_object_diff`) to generate the unified diff output. The diff-rendering service will carry this added complexity.

## Known Limitations

- **`list_commits()` is O(total repo history) per cache-miss.** Oxidized makes
  one commit per device change in a single shared repo, so the walk for one
  device's history traverses every commit ever made across all devices. At lab
  scale this is negligible; on a years-old production repo (100k+ commits) the
  first uncached load of a device's Config History tab can take seconds of
  pure-Python tree comparison.

  Mitigations in place: results are cached in Redis for 5 minutes
  (`CachedGitBackend`), and the dashboard no longer walks history at all — it
  is served from the denormalized `ConfigSnapshot` columns (see ADR 0004
  update), so the walk only ever runs for a single device at a time.

  Designed next step (deferred until there is a large repo to benchmark
  against): a `ConfigCommit` table populated incrementally by the indexing job
  (walk only commits newer than the last indexed SHA, fan out per device),
  making the history tab and diff picker pure DB reads and limiting git access
  to O(1) blob lookups for content and diffs.

## Alternatives Considered

- **GitPython**: Rejected because it requires the system `git` binary, creating excessive friction for installing the plugin in a standard `netbox-docker` deployment.
- **pygit2**: Rejected. `pygit2` provides fast bindings to `libgit2` (the C library GitHub uses internally) and has a richer API than `dulwich`. However, it requires the `libgit2` system library to be installed on the OS. This means it suffers from the exact same Dockerfile friction issue as GitPython, negating the primary benefit of choosing a Python-native solution.
