"""
Git repository interaction layer.

This module provides a pure-Python (Dulwich-based) interface to read the 
Oxidized bare git repository. 

Note: Caching is intentionally kept outside of this backend. `GitBackend` 
handles pure repository interactions; a separate `CachedGitBackend` wrapper 
or service layer will handle Redis caching to keep concerns separated.
"""

import datetime
import difflib
import os
from dataclasses import dataclass
from typing import Optional, List, Tuple

from dulwich.repo import Repo
from dulwich.errors import NotGitRepository
from dulwich.objects import Commit, Tree


# --- Exceptions ---

class GitBackendError(Exception):
    """Base exception for all git backend failures."""
    pass


class RepositoryNotFound(GitBackendError):
    """Raised when the git repository path does not exist."""
    pass


class InvalidRepository(GitBackendError):
    """Raised when the path exists but is not a valid git repository."""
    pass


class CommitNotFound(GitBackendError):
    """Raised when a specified commit SHA cannot be found in the repository."""
    pass


class FileNotFoundAtCommit(GitBackendError):
    """Raised when the specified file does not exist at the given commit."""
    pass


# --- Domain Models ---

@dataclass
class CommitMeta:
    """Represents metadata for a single commit."""
    sha: str
    author_name: str
    author_email: str
    subject: str  # First line of the commit message
    body: str     # Remainder of the commit message
    timestamp: datetime.datetime


@dataclass
class DiffHunk:
    """Represents a single unified diff hunk."""
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    # List of tuples: (marker, content)
    # marker is typically ' ', '+', or '-'
    lines: List[Tuple[str, str]]


@dataclass
class FileDiff:
    """Represents structured diff data for a single file between two commits."""
    old_sha: str
    new_sha: str
    filename: str
    hunks: List[DiffHunk]


# --- Backend Class ---

class GitBackend:
    def __init__(self, repo_path: str):
        self.repo_path = repo_path
        if not os.path.exists(repo_path):
            raise RepositoryNotFound(f"Repository path not found: {repo_path}")
            
        try:
            self.repo = Repo(repo_path)
        except NotGitRepository:
            raise InvalidRepository(f"Path is not a valid git repository: {repo_path}")

    def _parse_author(self, author_bytes: bytes) -> Tuple[str, str]:
        """Parses b'Name <email@example.com>' into ('Name', 'email@example.com')."""
        author_str = author_bytes.decode('utf-8', errors='replace')
        if '<' in author_str and '>' in author_str:
            name, email_part = author_str.split('<', 1)
            email = email_part.split('>', 1)[0]
            return name.strip(), email.strip()
        return author_str.strip(), ""

    def _parse_message(self, message_bytes: bytes) -> Tuple[str, str]:
        """Splits commit message into subject and body."""
        msg_str = message_bytes.decode('utf-8', errors='replace').strip()
        parts = msg_str.split('\n', 1)
        subject = parts[0].strip()
        body = parts[1].strip() if len(parts) > 1 else ""
        return subject, body

    def _commit_to_meta(self, commit: Commit) -> CommitMeta:
        """Converts a dulwich Commit object to CommitMeta."""
        name, email = self._parse_author(commit.author)
        subject, body = self._parse_message(commit.message)
        # Dulwich commit.commit_time is a Unix timestamp
        # timezone offset is in commit.commit_timezone (seconds west of UTC)
        # We'll use UTC for consistency
        dt = datetime.datetime.fromtimestamp(commit.commit_time, tz=datetime.timezone.utc)
        
        return CommitMeta(
            sha=commit.id.decode('ascii'),
            author_name=name,
            author_email=email,
            subject=subject,
            body=body,
            timestamp=dt
        )

    def _get_tree_item_sha(self, tree: Tree, filename: str) -> Optional[bytes]:
        """Finds the blob SHA for a specific filename in a tree."""
        # Oxidized repos are flat, so we don't need deep tree traversal for now,
        # but a simple lookup is safer.
        filename_bytes = filename.encode('utf-8')
        for item_name, _, sha in tree.iteritems():
            if item_name == filename_bytes:
                return sha
        return None

    def list_files(self) -> List[str]:
        """
        Returns a list of all files present in the HEAD commit.
        Useful for source validation.
        """
        try:
            head_sha = self.repo.head()
            head_commit = self.repo[head_sha]
            tree = self.repo[head_commit.tree]
        except KeyError:
             # Empty repository
             return []
             
        files = []
        for name, _, _ in tree.iteritems():
            files.append(name.decode('utf-8', errors='replace'))
        return sorted(files)

    def commit_exists(self, sha: str) -> bool:
        """
        Checks if a given commit SHA exists in the repository.
        Useful for URL/input validation.
        """
        try:
            sha_bytes = sha.encode('ascii')
            obj = self.repo[sha_bytes]
            return isinstance(obj, Commit)
        except (KeyError, ValueError):
            # KeyError if not found, ValueError if sha is invalid hex
            return False

    def list_commits(self, filename: str, limit: int = 50) -> List[CommitMeta]:
        """
        Returns a chronological list of commits (newest first) that modified the given file.
        """
        commits = []
        try:
            head_sha = self.repo.head()
        except KeyError:
            return [] # Empty repo

        walker = self.repo.get_walker(include=[head_sha])
        filename_bytes = filename.encode('utf-8')

        for entry in walker:
            commit = entry.commit
            
            # Check if file changed in this commit
            changed = False
            if not commit.parents:
                 # Initial commit
                 tree = self.repo[commit.tree]
                 if self._get_tree_item_sha(tree, filename):
                     changed = True
            else:
                 # Compare with first parent
                 parent_commit = self.repo[commit.parents[0]]
                 parent_tree = self.repo[parent_commit.tree]
                 curr_tree = self.repo[commit.tree]
                 
                 parent_blob_sha = self._get_tree_item_sha(parent_tree, filename)
                 curr_blob_sha = self._get_tree_item_sha(curr_tree, filename)
                 
                 if parent_blob_sha != curr_blob_sha:
                     changed = True
                     
            if changed:
                commits.append(self._commit_to_meta(commit))
                if len(commits) >= limit:
                    break
                    
        return commits

    def get_latest_commit(self, filename: str) -> Optional[CommitMeta]:
        """
        Returns the most recent commit that modified the given file.
        Returns None if the file doesn't exist in the repository's history.
        """
        commits = self.list_commits(filename, limit=1)
        return commits[0] if commits else None

    def latest_commit_per_file(self) -> dict:
        """
        Walk the repository history ONCE (newest-first) and return, for every
        file currently present at HEAD, the most recent commit that modified it:
        ``{filename: CommitMeta}``.

        Oxidized stores every device in one shared repo and commits one device
        per change, so calling get_latest_commit() per device is
        O(devices × history) — each call re-walks the whole repo. This does a
        single O(history) pass and stops early once every current file has been
        attributed, which is the hot path for the indexing job.

        Only top-level files are considered (Oxidized repos are flat); files
        deleted at HEAD are not returned.
        """
        try:
            head_sha = self.repo.head()
        except KeyError:
            return {}  # empty repository

        head_commit = self.repo[head_sha]
        head_tree = self.repo[head_commit.tree]
        remaining = {
            name.decode('utf-8', errors='replace')
            for name, _, _ in head_tree.iteritems()
        }
        result: dict = {}

        for entry in self.repo.get_walker(include=[head_sha]):
            if not remaining:
                break
            commit_meta = None
            for change in entry.changes():
                # Non-merge commits yield a flat list of TreeChange; merges can
                # yield a list per parent — normalise both shapes.
                change_group = change if isinstance(change, list) else [change]
                for tc in change_group:
                    target = tc.new if (tc.new and tc.new.path) else tc.old
                    if not target or not target.path:
                        continue
                    name = target.path.decode('utf-8', errors='replace')
                    if name in remaining:
                        if commit_meta is None:
                            commit_meta = self._commit_to_meta(entry.commit)
                        result[name] = commit_meta
                        remaining.discard(name)
        return result

    def get_file_content(self, filename: str, sha: str) -> str:
        """
        Returns the decoded string content of the file at the specific commit SHA.
        Raises CommitNotFound if the SHA is invalid.
        Raises FileNotFoundAtCommit if the file doesn't exist at that SHA.
        """
        try:
            sha_bytes = sha.encode('ascii')
            commit = self.repo[sha_bytes]
            if not isinstance(commit, Commit):
                raise CommitNotFound(f"SHA {sha} is not a commit")
        except (KeyError, ValueError):
            raise CommitNotFound(f"Commit not found: {sha}")

        tree = self.repo[commit.tree]
        blob_sha = self._get_tree_item_sha(tree, filename)
        
        if not blob_sha:
            raise FileNotFoundAtCommit(f"File {filename} not found at commit {sha}")
            
        blob = self.repo[blob_sha]
        return blob.data.decode('utf-8', errors='replace')

    @staticmethod
    def _parse_hunk_header(info: str) -> Tuple[int, int]:
        """Parse '-M,N' or '+M,N' hunk header fragment into (start, lines)."""
        if ',' in info:
            s, l = info.split(',', 1)
            return int(s), int(l)
        return int(info), 1

    def get_diff(self, filename: str, sha_old: str, sha_new: str) -> FileDiff:
        """
        Return a structured diff for filename between two commits.
        Handles file creation (missing at sha_old) and deletion (missing at sha_new).
        Raises CommitNotFound or FileNotFoundAtCommit as appropriate.
        """
        old_content: List[str] = []
        new_content: List[str] = []

        try:
            old_content = self.get_file_content(filename, sha_old).splitlines()
        except FileNotFoundAtCommit:
            pass  # file created in sha_new

        try:
            new_content = self.get_file_content(filename, sha_new).splitlines()
        except FileNotFoundAtCommit:
            pass  # file deleted in sha_new

        if not old_content and not new_content:
            raise FileNotFoundAtCommit(
                f"File '{filename}' not found at either commit {sha_old} or {sha_new}"
            )

        raw_lines = list(difflib.unified_diff(
            old_content, new_content,
            fromfile=f"a/{filename}", tofile=f"b/{filename}",
            lineterm="",
        ))

        hunks: List[DiffHunk] = []
        current_hunk: Optional[DiffHunk] = None

        for line in raw_lines:
            if line.startswith('---') or line.startswith('+++'):
                continue
            if line.startswith('@@'):
                if current_hunk:
                    hunks.append(current_hunk)
                parts = line.split(' ')
                old_start, old_lines = self._parse_hunk_header(parts[1][1:])
                new_start, new_lines = self._parse_hunk_header(parts[2][1:])
                current_hunk = DiffHunk(
                    old_start=old_start, old_lines=old_lines,
                    new_start=new_start, new_lines=new_lines,
                    lines=[],
                )
            elif current_hunk is not None:
                marker = line[0] if line else ' '
                content = line[1:] if line else ''
                current_hunk.lines.append((marker, content))

        if current_hunk:
            hunks.append(current_hunk)

        return FileDiff(old_sha=sha_old, new_sha=sha_new, filename=filename, hunks=hunks)