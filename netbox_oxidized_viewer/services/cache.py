import hashlib

from django.core.cache import cache

from .git_backend import CommitMeta, FileDiff, GitBackend

# Distinguishes "key absent" from "key present, value is None". Without it,
# get_latest_commit(None) — a device with no backups — would never be treated as
# cached and would re-walk the full repo history on every request.
_MISS = object()

# Config bodies and diffs are plaintext device configuration; they must not
# linger in a shared Redis. Metadata is cheap to recompute too.
BODY_TTL = 300


class CachedGitBackend:
    """
    A wrapper around GitBackend that caches expensive operations using Redis.
    NetBox configures the default cache automatically.
    """

    def __init__(self, backend: GitBackend):
        self.backend = backend

    def _cache_key(self, prefix: str, *args) -> str:
        key_content = ':'.join(str(a) for a in args)
        hashed = hashlib.md5(key_content.encode('utf-8')).hexdigest()
        return f'oxidized_viewer:{prefix}:{hashed}'

    def list_commits(self, filename: str, limit: int = 50) -> list[CommitMeta]:
        # HEAD can move between indexing runs, so keep this short (5 minutes).
        key = self._cache_key('list_commits', self.backend.repo_path, filename, limit)
        cached = cache.get(key, _MISS)
        if cached is not _MISS:
            return cached
        result = self.backend.list_commits(filename, limit)
        cache.set(key, result, timeout=300)
        return result

    def get_latest_commit(self, filename: str) -> CommitMeta | None:
        key = self._cache_key('latest_commit', self.backend.repo_path, filename)
        cached = cache.get(key, _MISS)
        if cached is not _MISS:
            return cached
        result = self.backend.get_latest_commit(filename)
        cache.set(key, result, timeout=300)
        return result

    def get_file_content(self, filename: str, sha: str) -> str:
        # Immutable per SHA, but this is plaintext config in Redis: keep it short.
        key = self._cache_key('file_content', self.backend.repo_path, filename, sha)
        cached = cache.get(key, _MISS)
        if cached is not _MISS:
            return cached
        result = self.backend.get_file_content(filename, sha)
        cache.set(key, result, timeout=BODY_TTL)
        return result

    def get_diff(self, filename: str, sha_old: str, sha_new: str) -> FileDiff:
        # Immutable per SHA pair, but it carries config text: keep it short.
        key = self._cache_key('file_diff', self.backend.repo_path, filename, sha_old, sha_new)
        cached = cache.get(key, _MISS)
        if cached is not _MISS:
            return cached
        result = self.backend.get_diff(filename, sha_old, sha_new)
        cache.set(key, result, timeout=BODY_TTL)
        return result
