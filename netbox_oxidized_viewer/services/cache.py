import hashlib
from typing import List, Optional

from django.core.cache import cache

from .git_backend import CommitMeta, FileDiff, GitBackend

# Distinguishes "key absent" from "key present, value is None". Without it,
# get_latest_commit(None) — a device with no backups — would never be treated as
# cached and would re-walk the full repo history on every request.
_MISS = object()


class CachedGitBackend:
    """
    A wrapper around GitBackend that caches expensive operations using Redis.
    NetBox configures the default cache automatically.
    """
    def __init__(self, backend: GitBackend):
        self.backend = backend

    def _cache_key(self, prefix: str, *args) -> str:
        key_content = ":".join(str(a) for a in args)
        hashed = hashlib.md5(key_content.encode('utf-8')).hexdigest()
        return f"oxidized_viewer:{prefix}:{hashed}"

    def list_commits(self, filename: str, limit: int = 50) -> List[CommitMeta]:
        # HEAD can move between indexing runs, so keep this short (5 minutes).
        key = self._cache_key("list_commits", self.backend.repo_path, filename, limit)
        cached = cache.get(key, _MISS)
        if cached is not _MISS:
            return cached
        result = self.backend.list_commits(filename, limit)
        cache.set(key, result, timeout=300)
        return result

    def get_latest_commit(self, filename: str) -> Optional[CommitMeta]:
        key = self._cache_key("latest_commit", self.backend.repo_path, filename)
        cached = cache.get(key, _MISS)
        if cached is not _MISS:
            return cached
        result = self.backend.get_latest_commit(filename)
        cache.set(key, result, timeout=300)
        return result

    def get_file_content(self, filename: str, sha: str) -> str:
        # File content at a specific SHA is immutable — cache for 24 hours.
        key = self._cache_key("file_content", self.backend.repo_path, filename, sha)
        cached = cache.get(key, _MISS)
        if cached is not _MISS:
            return cached
        result = self.backend.get_file_content(filename, sha)
        cache.set(key, result, timeout=86400)
        return result

    def get_diff(self, filename: str, sha_old: str, sha_new: str) -> FileDiff:
        # Diff between two immutable SHAs is itself immutable — cache for 24 hours.
        key = self._cache_key("file_diff", self.backend.repo_path, filename, sha_old, sha_new)
        cached = cache.get(key, _MISS)
        if cached is not _MISS:
            return cached
        result = self.backend.get_diff(filename, sha_old, sha_new)
        cache.set(key, result, timeout=86400)
        return result
