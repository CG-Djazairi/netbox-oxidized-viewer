"""
Unit tests for the Dulwich-based GitBackend.

Written as unittest.TestCase (not pytest functions) so Django's test runner
(`manage.py test netbox_oxidized_viewer`, the NetBox convention) collects them.
The fixtures build real on-disk dulwich repositories by writing raw
Blob/Tree/Commit objects — fast (no git subprocess) and exercising the exact
object-database reads production uses.
"""

import datetime
import os
import shutil
import tempfile
import unittest

from dulwich.objects import Blob, Commit, Tree
from dulwich.repo import Repo

from netbox_oxidized_viewer.services.git_backend import (
    CommitNotFound,
    FileNotFoundAtCommit,
    GitBackend,
    InvalidRepository,
    RepositoryNotFound,
)

# Optional integration repo: export OXIDIZED_LAB_REPO=/path/to/bare/repo to run.
LAB_REPO_PATH = os.environ.get('OXIDIZED_LAB_REPO', '')


def _commit(repo, tree, parents, author, message, when):
    c = Commit()
    c.tree = tree.id
    c.parents = parents
    c.author = c.committer = author
    c.commit_time = c.author_time = int(when.timestamp())
    c.commit_timezone = c.author_timezone = 0
    c.encoding = b'UTF-8'
    c.message = message
    repo.object_store.add_object(c)
    return c


def _tree(repo, entries):
    """entries: list of (name_bytes, blob). Returns the added Tree."""
    t = Tree()
    for name, blob in entries:
        repo.object_store.add_object(blob)
        t.add(name, 0o100644, blob.id)
    repo.object_store.add_object(t)
    return t


class GitBackendTestCase(unittest.TestCase):
    """Three commits on file1.txt: create, modify, then delete + add file2."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        repo = Repo.init(self.temp_dir)

        t1 = _tree(repo, [(b'file1.txt', Blob.from_string(b'line 1\nline 2\nline 3\n'))])
        c1 = _commit(
            repo,
            t1,
            [],
            b'Test Author <test@example.com>',
            b'Initial commit\n\nAdded file1.txt',
            datetime.datetime(2023, 1, 1, 12, 0, tzinfo=datetime.UTC),
        )
        repo.refs[b'refs/heads/master'] = c1.id

        t2 = _tree(repo, [(b'file1.txt', Blob.from_string(b'line 1\nline 2 changed\nline 3\nline 4 added\n'))])
        c2 = _commit(
            repo,
            t2,
            [c1.id],
            b'Test Author <test@example.com>',
            b'Second commit\n\nModified file1.txt',
            datetime.datetime(2023, 1, 2, 12, 0, tzinfo=datetime.UTC),
        )
        repo.refs[b'refs/heads/master'] = c2.id

        t3 = _tree(repo, [(b'file2.txt', Blob.from_string(b'new file content\n'))])
        c3 = _commit(
            repo,
            t3,
            [c2.id],
            b'Other Author <other@example.com>',
            b'Third commit\n\nDeleted file1.txt, added file2.txt',
            datetime.datetime(2023, 1, 3, 12, 0, tzinfo=datetime.UTC),
        )
        repo.refs[b'refs/heads/master'] = c3.id
        repo.refs[b'HEAD'] = c3.id

        self.c1 = c1.id.decode('ascii')
        self.c2 = c2.id.decode('ascii')
        self.c3 = c3.id.decode('ascii')

    def test_init_errors(self):
        with self.assertRaises(RepositoryNotFound):
            GitBackend('/path/that/does/not/exist/12345')
        with tempfile.TemporaryDirectory() as empty_dir:
            with self.assertRaises(InvalidRepository):
                GitBackend(empty_dir)

    def test_list_files(self):
        backend = GitBackend(self.temp_dir)
        # HEAD is at c3, which only has file2.txt
        self.assertEqual(backend.list_files(), ['file2.txt'])

    def test_commit_exists(self):
        backend = GitBackend(self.temp_dir)
        self.assertTrue(backend.commit_exists(self.c1))
        self.assertFalse(backend.commit_exists('0' * 40))
        self.assertFalse(backend.commit_exists('invalid-sha'))

    def test_get_file_content(self):
        backend = GitBackend(self.temp_dir)
        self.assertEqual(backend.get_file_content('file1.txt', self.c1), 'line 1\nline 2\nline 3\n')
        self.assertEqual(
            backend.get_file_content('file1.txt', self.c2),
            'line 1\nline 2 changed\nline 3\nline 4 added\n',
        )
        with self.assertRaises(FileNotFoundAtCommit):
            backend.get_file_content('file1.txt', self.c3)
        with self.assertRaises(CommitNotFound):
            backend.get_file_content('file1.txt', '0' * 40)

    def test_list_commits(self):
        backend = GitBackend(self.temp_dir)
        commits = backend.list_commits('file1.txt')
        self.assertEqual(len(commits), 3)  # c3 deletes, c2 modifies, c1 adds
        self.assertEqual(commits[0].sha, self.c3)
        self.assertEqual(commits[1].sha, self.c2)
        self.assertEqual(commits[2].sha, self.c1)
        self.assertEqual(commits[2].author_name, 'Test Author')
        self.assertEqual(commits[2].author_email, 'test@example.com')
        self.assertEqual(commits[2].subject, 'Initial commit')
        self.assertEqual(commits[2].body, 'Added file1.txt')

    def test_list_commits_respects_limit(self):
        backend = GitBackend(self.temp_dir)
        self.assertEqual(len(backend.list_commits('file1.txt', limit=2)), 2)

    def test_get_latest_commit(self):
        backend = GitBackend(self.temp_dir)
        commit = backend.get_latest_commit('file1.txt')
        self.assertIsNotNone(commit)
        self.assertEqual(commit.sha, self.c3)  # deletion is still a change

    def test_get_latest_commit_missing_file(self):
        backend = GitBackend(self.temp_dir)
        self.assertIsNone(backend.get_latest_commit('never-existed.txt'))

    def test_get_diff(self):
        backend = GitBackend(self.temp_dir)
        diff = backend.get_diff('file1.txt', self.c1, self.c2)
        self.assertEqual(diff.old_sha, self.c1)
        self.assertEqual(diff.new_sha, self.c2)
        self.assertEqual(diff.filename, 'file1.txt')
        self.assertGreater(len(diff.hunks), 0)

        diff_del = backend.get_diff('file1.txt', self.c2, self.c3)
        self.assertGreater(len(diff_del.hunks), 0)

        with self.assertRaises(FileNotFoundAtCommit):
            backend.get_diff('nonexistent.txt', self.c1, self.c2)

    def test_latest_commit_per_file(self):
        backend = GitBackend(self.temp_dir)
        mapping = backend.latest_commit_per_file()
        # HEAD is c3, which contains only file2.txt (file1 was deleted there).
        self.assertEqual(set(mapping), {'file2.txt'})
        self.assertEqual(mapping['file2.txt'].sha, self.c3)

    def test_latest_commit_per_file_attributes_newest_change(self):
        """A file changed in a later commit is attributed to that commit, while
        an unchanged file keeps its original commit."""
        repo = Repo(self.temp_dir)
        # Two files that both survive to HEAD: 'stable' added once, 'churn' changed twice.
        t_a = _tree(
            repo,
            [
                (b'stable', Blob.from_string(b'unchanged\n')),
                (b'churn', Blob.from_string(b'c1\n')),
            ],
        )
        head = repo[repo.head()]
        c_a = _commit(repo, t_a, [head.id], b'A <a@x>', b'add both', datetime.datetime(2023, 2, 1, tzinfo=datetime.UTC))
        repo.refs[b'refs/heads/master'] = c_a.id
        t_b = _tree(
            repo,
            [
                (b'stable', Blob.from_string(b'unchanged\n')),
                (b'churn', Blob.from_string(b'c2\n')),
            ],
        )
        c_b = _commit(
            repo, t_b, [c_a.id], b'A <a@x>', b'change churn', datetime.datetime(2023, 3, 1, tzinfo=datetime.UTC)
        )
        repo.refs[b'refs/heads/master'] = c_b.id
        repo.refs[b'HEAD'] = c_b.id

        mapping = GitBackend(self.temp_dir).latest_commit_per_file()
        self.assertEqual(mapping['churn'].sha, c_b.id.decode('ascii'))
        self.assertEqual(mapping['stable'].sha, c_a.id.decode('ascii'))

    def test_non_utf8_content_is_replaced_not_raised(self):
        """Vendor banners in latin-1 must not 500 the Config History tab."""
        repo = Repo(self.temp_dir)
        blob = Blob.from_string(b'hostname r\xff\xfeuter\n')  # invalid UTF-8
        t = _tree(repo, [(b'latin1.cfg', blob)])
        head = repo[repo.head()]
        c = _commit(
            repo,
            t,
            [head.id],
            b'A <a@x>',
            b'latin1',
            datetime.datetime(2023, 1, 4, 12, 0, tzinfo=datetime.UTC),
        )
        repo.refs[b'refs/heads/master'] = c.id
        repo.refs[b'HEAD'] = c.id
        backend = GitBackend(self.temp_dir)
        content = backend.get_file_content('latin1.cfg', c.id.decode('ascii'))
        self.assertIn('hostname r', content)  # decoded with errors='replace'


class EmptyRepoTestCase(unittest.TestCase):
    """A freshly-initialized Oxidized repo before its first commit is a real
    day-one state; list_files/list_commits must return [] rather than raise."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)
        Repo.init(self.temp_dir)

    def test_list_files_empty(self):
        self.assertEqual(GitBackend(self.temp_dir).list_files(), [])

    def test_list_commits_empty(self):
        self.assertEqual(GitBackend(self.temp_dir).list_commits('anything.cfg'), [])

    def test_get_latest_commit_empty(self):
        self.assertIsNone(GitBackend(self.temp_dir).get_latest_commit('anything.cfg'))

    def test_latest_commit_per_file_empty(self):
        self.assertEqual(GitBackend(self.temp_dir).latest_commit_per_file(), {})


@unittest.skipUnless(
    LAB_REPO_PATH and os.path.exists(LAB_REPO_PATH),
    'Set OXIDIZED_LAB_REPO to a bare repo path to run lab integration',
)
class LabIntegrationTestCase(unittest.TestCase):
    def test_lab_integration(self):
        backend = GitBackend(LAB_REPO_PATH)
        files = backend.list_files()
        self.assertGreater(len(files), 0)

        filename = files[0]
        latest = backend.get_latest_commit(filename)
        self.assertIsNotNone(latest)
        self.assertTrue(backend.commit_exists(latest.sha))

        commits = backend.list_commits(filename, limit=2)
        self.assertGreaterEqual(len(commits), 1)
        if len(commits) >= 2:
            diff = backend.get_diff(filename, commits[1].sha, commits[0].sha)
            self.assertEqual(diff.filename, filename)
