"""
Tests for the update_config_snapshots indexing job, exercised against a real
on-disk dulwich repository whose files are named after device names.
"""

import datetime
import shutil
import tempfile

from django.contrib.postgres.search import SearchQuery
from django.test import TestCase

from dulwich.objects import Blob, Commit, Tree
from dulwich.repo import Repo

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site

from netbox_oxidized_viewer.models import ConfigSnapshot, OxidizedSource
from netbox_oxidized_viewer.tasks import update_config_snapshots


def _build_repo(path, files, parent=None, when=(2024, 1, 1)):
    """Commit a {filename: content} mapping to the repo at `path`."""
    repo = Repo.init(path) if parent is None else Repo(path)

    tree = Tree()
    for name, content in files.items():
        blob = Blob.from_string(content.encode('utf-8'))
        repo.object_store.add_object(blob)
        tree.add(name.encode('utf-8'), 0o100644, blob.id)
    repo.object_store.add_object(tree)

    commit = Commit()
    commit.tree = tree.id
    if parent:
        # dulwich expects parent SHAs as bytes, not the decoded hex string.
        commit.parents = [parent.encode('ascii') if isinstance(parent, str) else parent]
    commit.author = commit.committer = b"Oxidized <oxidized@example.com>"
    ts = int(datetime.datetime(*when, tzinfo=datetime.timezone.utc).timestamp())
    commit.commit_time = commit.author_time = ts
    commit.commit_timezone = commit.author_timezone = 0
    commit.encoding = b"UTF-8"
    commit.message = b"backup\n"
    repo.object_store.add_object(commit)
    repo.refs[b"refs/heads/master"] = commit.id
    repo.refs[b"HEAD"] = commit.id
    return commit.id.decode('ascii')


class TestUpdateConfigSnapshots(TestCase):

    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model='SR Linux', slug='sr-linux'
        )
        role = DeviceRole.objects.create(name='Router', slug='router')
        cls.spine = Device.objects.create(
            name='spine1', site=site, device_type=device_type, role=role
        )
        cls.leaf = Device.objects.create(
            name='leaf1', site=site, device_type=device_type, role=role
        )
        # A device with no matching file in the repo — must be skipped silently.
        cls.orphan = Device.objects.create(
            name='no-backup', site=site, device_type=device_type, role=role
        )

    def setUp(self):
        self.repo_path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo_path, ignore_errors=True)
        self.sha1 = _build_repo(self.repo_path, {
            'spine1': 'set / interface ethernet-1/1 admin-state enable\n',
            'leaf1': 'set / network-instance default protocols bgp\n',
        })
        self.source = OxidizedSource.objects.create(
            name='Lab', git_repo_path=self.repo_path
        )

    def test_creates_snapshots_for_matched_devices(self):
        update_config_snapshots(source_pk=self.source.pk)
        snaps = ConfigSnapshot.objects.filter(source=self.source)
        self.assertEqual(set(snaps.values_list('device__name', flat=True)),
                         {'spine1', 'leaf1'})

    def test_commit_metadata_denormalized(self):
        # The dashboard is served from these columns — they must mirror the
        # indexed commit, not be left at their defaults.
        update_config_snapshots(source_pk=self.source.pk)
        snap = ConfigSnapshot.objects.get(device=self.spine)
        self.assertEqual(snap.commit_subject, 'backup')
        self.assertEqual(
            snap.commit_timestamp,
            datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc),
        )

    def test_backfills_metadata_for_pre_0006_rows(self):
        # Rows indexed before the metadata columns existed have the same SHA
        # but NULL commit_timestamp — the job must update them, not skip them.
        update_config_snapshots(source_pk=self.source.pk)
        ConfigSnapshot.objects.filter(device=self.spine).update(
            commit_timestamp=None, commit_subject=''
        )
        update_config_snapshots(source_pk=self.source.pk)
        snap = ConfigSnapshot.objects.get(device=self.spine)
        self.assertIsNotNone(snap.commit_timestamp)
        self.assertEqual(snap.commit_subject, 'backup')

    def test_orphan_device_not_indexed(self):
        update_config_snapshots(source_pk=self.source.pk)
        self.assertFalse(
            ConfigSnapshot.objects.filter(device=self.orphan).exists()
        )

    def test_search_vector_populated(self):
        update_config_snapshots(source_pk=self.source.pk)
        sq = SearchQuery('interface', config='simple')
        hits = ConfigSnapshot.objects.filter(source=self.source, search_vector=sq)
        self.assertEqual(set(hits.values_list('device__name', flat=True)),
                         {'spine1'})

    def test_rerun_without_change_keeps_same_sha(self):
        update_config_snapshots(source_pk=self.source.pk)
        before = dict(ConfigSnapshot.objects.values_list('device__name', 'commit_sha'))
        update_config_snapshots(source_pk=self.source.pk)
        after = dict(ConfigSnapshot.objects.values_list('device__name', 'commit_sha'))
        self.assertEqual(before, after)

    def test_new_commit_updates_snapshot(self):
        update_config_snapshots(source_pk=self.source.pk)
        old_sha = ConfigSnapshot.objects.get(device=self.spine).commit_sha

        # Commit a changed config for spine1.
        new_sha = _build_repo(
            self.repo_path,
            {'spine1': 'set / interface ethernet-1/1 admin-state disable\n',
             'leaf1': 'set / network-instance default protocols bgp\n'},
            parent=self.sha1,
            when=(2024, 2, 1),
        )
        update_config_snapshots(source_pk=self.source.pk)

        snap = ConfigSnapshot.objects.get(device=self.spine)
        self.assertNotEqual(snap.commit_sha, old_sha)
        self.assertEqual(snap.commit_sha, new_sha)
        self.assertIn('disable', snap.content)
