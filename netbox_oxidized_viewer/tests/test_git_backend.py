import datetime
import os
import tempfile
import pytest

from dulwich.repo import Repo
from dulwich.objects import Blob, Tree, Commit

from netbox_oxidized_viewer.services.git_backend import (
    GitBackend,
    GitBackendError,
    RepositoryNotFound,
    InvalidRepository,
    CommitNotFound,
    FileNotFoundAtCommit,
    CommitMeta,
    FileDiff
)


@pytest.fixture
def temp_git_repo():
    """Creates a temporary git repository using dulwich for testing."""
    temp_dir = tempfile.mkdtemp()
    repo = Repo.init(temp_dir)
    
    # --- Commit 1: Initial creation ---
    # Create file1
    b1 = Blob.from_string(b"line 1\nline 2\nline 3\n")
    repo.object_store.add_object(b1)
    
    t1 = Tree()
    t1.add(b"file1.txt", 0o100644, b1.id)
    repo.object_store.add_object(t1)
    
    c1 = Commit()
    c1.tree = t1.id
    c1.author = c1.committer = b"Test Author <test@example.com>"
    c1.commit_time = c1.author_time = int(datetime.datetime(2023, 1, 1, 12, 0, tzinfo=datetime.timezone.utc).timestamp())
    c1.commit_timezone = c1.author_timezone = 0
    c1.encoding = b"UTF-8"
    c1.message = b"Initial commit\n\nAdded file1.txt"
    repo.object_store.add_object(c1)
    repo.refs[b"refs/heads/master"] = c1.id
    
    # --- Commit 2: Modify file1 ---
    b2 = Blob.from_string(b"line 1\nline 2 changed\nline 3\nline 4 added\n")
    repo.object_store.add_object(b2)
    
    t2 = Tree()
    t2.add(b"file1.txt", 0o100644, b2.id)
    repo.object_store.add_object(t2)
    
    c2 = Commit()
    c2.tree = t2.id
    c2.parents = [c1.id]
    c2.author = c2.committer = b"Test Author <test@example.com>"
    c2.commit_time = c2.author_time = int(datetime.datetime(2023, 1, 2, 12, 0, tzinfo=datetime.timezone.utc).timestamp())
    c2.commit_timezone = c2.author_timezone = 0
    c2.encoding = b"UTF-8"
    c2.message = b"Second commit\n\nModified file1.txt"
    repo.object_store.add_object(c2)
    repo.refs[b"refs/heads/master"] = c2.id

    # --- Commit 3: Delete file1, create file2 ---
    b3 = Blob.from_string(b"new file content\n")
    repo.object_store.add_object(b3)
    
    t3 = Tree()
    t3.add(b"file2.txt", 0o100644, b3.id)
    repo.object_store.add_object(t3)
    
    c3 = Commit()
    c3.tree = t3.id
    c3.parents = [c2.id]
    c3.author = c3.committer = b"Other Author <other@example.com>"
    c3.commit_time = c3.author_time = int(datetime.datetime(2023, 1, 3, 12, 0, tzinfo=datetime.timezone.utc).timestamp())
    c3.commit_timezone = c3.author_timezone = 0
    c3.encoding = b"UTF-8"
    c3.message = b"Third commit\n\nDeleted file1.txt, added file2.txt"
    repo.object_store.add_object(c3)
    repo.refs[b"refs/heads/master"] = c3.id

    # Update HEAD
    repo.refs[b"HEAD"] = c3.id

    yield {
        "path": temp_dir,
        "c1": c1.id.decode('ascii'),
        "c2": c2.id.decode('ascii'),
        "c3": c3.id.decode('ascii')
    }
    
    # Cleanup could be added here, but tempfile handles basic isolation.
    import shutil
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_init_errors():
    with pytest.raises(RepositoryNotFound):
        GitBackend("/path/that/does/not/exist/12345")
        
    with tempfile.TemporaryDirectory() as empty_dir:
        with pytest.raises(InvalidRepository):
            GitBackend(empty_dir)


def test_list_files(temp_git_repo):
    backend = GitBackend(temp_git_repo["path"])
    # HEAD is at c3, which only has file2.txt
    assert backend.list_files() == ["file2.txt"]


def test_commit_exists(temp_git_repo):
    backend = GitBackend(temp_git_repo["path"])
    assert backend.commit_exists(temp_git_repo["c1"]) is True
    assert backend.commit_exists("0000000000000000000000000000000000000000") is False
    assert backend.commit_exists("invalid-sha") is False


def test_get_file_content(temp_git_repo):
    backend = GitBackend(temp_git_repo["path"])
    
    content_c1 = backend.get_file_content("file1.txt", temp_git_repo["c1"])
    assert content_c1 == "line 1\nline 2\nline 3\n"
    
    content_c2 = backend.get_file_content("file1.txt", temp_git_repo["c2"])
    assert content_c2 == "line 1\nline 2 changed\nline 3\nline 4 added\n"
    
    with pytest.raises(FileNotFoundAtCommit):
        backend.get_file_content("file1.txt", temp_git_repo["c3"])

    with pytest.raises(CommitNotFound):
        backend.get_file_content("file1.txt", "0000000000000000000000000000000000000000")


def test_list_commits(temp_git_repo):
    backend = GitBackend(temp_git_repo["path"])
    
    commits = backend.list_commits("file1.txt")
    assert len(commits) == 3  # c3 deletes it, c2 modifies it, c1 adds it
    assert commits[0].sha == temp_git_repo["c3"]
    assert commits[1].sha == temp_git_repo["c2"]
    assert commits[2].sha == temp_git_repo["c1"]
    
    # Check parsing on c1
    assert commits[2].author_name == "Test Author"
    assert commits[2].author_email == "test@example.com"
    assert commits[2].subject == "Initial commit"
    assert commits[2].body == "Added file1.txt"


def test_get_latest_commit(temp_git_repo):
    backend = GitBackend(temp_git_repo["path"])
    
    commit = backend.get_latest_commit("file1.txt")
    assert commit is not None
    assert commit.sha == temp_git_repo["c3"]  # Deletion is still a change


def test_get_diff(temp_git_repo):
    backend = GitBackend(temp_git_repo["path"])
    
    # Diff c1 to c2 (modification)
    diff = backend.get_diff("file1.txt", temp_git_repo["c1"], temp_git_repo["c2"])
    assert diff.old_sha == temp_git_repo["c1"]
    assert diff.new_sha == temp_git_repo["c2"]
    assert diff.filename == "file1.txt"
    assert len(diff.hunks) > 0
    
    # Diff c2 to c3 (deletion)
    diff_del = backend.get_diff("file1.txt", temp_git_repo["c2"], temp_git_repo["c3"])
    # difflib might generate multiple hunks or one big deletion
    assert len(diff_del.hunks) > 0
    
    # Error: File not found at either commit
    with pytest.raises(FileNotFoundAtCommit):
        backend.get_diff("nonexistent.txt", temp_git_repo["c1"], temp_git_repo["c2"])

# --- Integration Tests using Lab Repo ---
@pytest.mark.skipif(not os.path.exists("/media/semch/Jam1/oxidized-lab/oxidized/git-output/"),
                    reason="Lab repository not available")
def test_lab_integration():
    """Tests against the actual bare git repository."""
    repo_path = "/media/semch/Jam1/oxidized-lab/oxidized/git-output/"
    backend = GitBackend(repo_path)
    
    files = backend.list_files()
    assert len(files) > 0
    
    # Use one of the files found
    filename = files[0]
    
    latest = backend.get_latest_commit(filename)
    assert latest is not None
    assert backend.commit_exists(latest.sha) is True
    
    commits = backend.list_commits(filename, limit=2)
    assert len(commits) >= 1
    
    if len(commits) >= 2:
        sha_new = commits[0].sha
        sha_old = commits[1].sha
        diff = backend.get_diff(filename, sha_old, sha_new)
        assert diff.filename == filename
