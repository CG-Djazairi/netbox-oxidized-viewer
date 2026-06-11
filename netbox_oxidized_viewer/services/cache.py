import hashlib
from typing import Optional, List
from django.core.cache import cache

from .git_backend import GitBackend, CommitMeta, FileDiff

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
        # Caching commits for a short time (5 minutes)
        key = self._cache_key("list_commits", self.backend.repo_path, filename, limit)
        cached = cache.get(key)
        if cached is not None:
            return cached
            
        result = self.backend.list_commits(filename, limit)
        cache.set(key, result, timeout=300)
        return result
        
    def get_latest_commit(self, filename: str) -> Optional[CommitMeta]:
        # Caching latest commit for a short time (5 minutes)
        key = self._cache_key("latest_commit", self.backend.repo_path, filename)
        cached = cache.get(key)
        if cached is not None:
            return cached
            
        result = self.backend.get_latest_commit(filename)
        cache.set(key, result, timeout=300)
        return result
        
    def get_file_content(self, filename: str, sha: str) -> str:
        # File content at a specific SHA is immutable, cache for 24 hours
        key = self._cache_key("file_content", self.backend.repo_path, filename, sha)
        cached = cache.get(key)
        if cached is not None:
            return cached
            
        result = self.backend.get_file_content(filename, sha)
        cache.set(key, result, timeout=86400)
        return result
        
    def get_diff(self, filename: str, sha_old: str, sha_new: str) -> FileDiff:
        # Diff between two specific SHAs is immutable, cache for 24 hours
        key = self._cache_key("file_diff", self.backend.repo_path, filename, sha_old, sha_new)
        cached = cache.get(key)
        if cached is not None:
            return cached
            
        result = self.backend.get_diff(filename, sha_old, sha_new)
        cache.set(key, result, timeout=86400)
        return result
        
    def list_files(self) -> List[str]:
        # Can change, cache for short duration (5 minutes)
        key = self._cache_key("list_files", self.backend.repo_path)
        cached = cache.get(key)
        if cached is not None:
            return cached
            
        result = self.backend.list_files()
        cache.set(key, result, timeout=300)
        return result

    def commit_exists(self, sha: str) -> bool:
        # Immutability helps, but negative results should not be cached long
        key = self._cache_key("commit_exists", self.backend.repo_path, sha)
        cached = cache.get(key)
        if cached is not None:
            return cached
            
        result = self.backend.commit_exists(sha)
        if result:
            cache.set(key, result, timeout=86400)
        else:
            cache.set(key, result, timeout=60)
        return result

    def invalidate_commits_for_file(self, filename: str):
        """
        Manually invalidate commit caches for a file (e.g. after a manual poll).
        Because limit might vary for list_commits, we clear common keys.
        """
        cache.delete(self._cache_key("latest_commit", self.backend.repo_path, filename))
        cache.delete(self._cache_key("list_commits", self.backend.repo_path, filename, 50))
        # The background task or next poll will fetch fresh data.
