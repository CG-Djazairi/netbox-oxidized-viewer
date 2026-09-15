"""
Object-level RBAC tests for the raw download views.

These are plain Django Views (not NetBox generic views), so they must apply
Device.objects.restrict() themselves — a user without view permission on a
device must get a 404 for its config, any historical commit, and any diff.
"""

import datetime
import shutil
import tempfile
from unittest import mock

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import RequestFactory, TestCase
from django.utils import timezone

from netbox_oxidized_viewer.models import ConfigCommitNote, ConfigSnapshot, OxidizedSource
from netbox_oxidized_viewer.views import (
    AddCommitNoteView,
    CommitConfigDownloadView,
    ConfigSearchView,
    DashboardView,
    DeviceConfigDownloadView,
    DeviceSyncView,
    DiffDownloadView,
    SourceReindexView,
)

from .test_tasks import _build_repo


class TestDownloadViewPermissions(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='SR Linux', slug='sr-linux')
        role = DeviceRole.objects.create(name='Router', slug='router')
        cls.device = Device.objects.create(name='spine1', site=site, device_type=device_type, role=role)
        cls.superuser = get_user_model().objects.create_superuser('dl-admin')
        cls.plain_user = get_user_model().objects.create_user('dl-nobody')

    def setUp(self):
        self.repo_path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo_path, ignore_errors=True)
        self.sha1 = _build_repo(
            self.repo_path,
            {
                'spine1': 'set / interface ethernet-1/1 admin-state enable\n',
            },
        )
        self.sha2 = _build_repo(
            self.repo_path,
            {
                'spine1': 'set / interface ethernet-1/1 admin-state disable\n',
            },
            parent=self.sha1,
            when=(2024, 2, 1),
        )
        OxidizedSource.objects.create(name='Lab', git_repo_path=self.repo_path)

    def _get(self, view_cls, user, **url_kwargs):
        request = RequestFactory().get('/')
        request.user = user
        return view_cls.as_view()(request, **url_kwargs)

    # --- current config ---

    def test_config_download_allowed_for_permitted_user(self):
        response = self._get(DeviceConfigDownloadView, self.superuser, pk=self.device.pk)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'admin-state disable', response.content)

    def test_config_download_denied_without_view_permission(self):
        with self.assertRaises(Http404):
            self._get(DeviceConfigDownloadView, self.plain_user, pk=self.device.pk)

    # --- historical commit ---

    def test_commit_download_allowed_for_permitted_user(self):
        response = self._get(CommitConfigDownloadView, self.superuser, pk=self.device.pk, sha=self.sha1)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'admin-state enable', response.content)

    def test_commit_download_denied_without_view_permission(self):
        with self.assertRaises(Http404):
            self._get(CommitConfigDownloadView, self.plain_user, pk=self.device.pk, sha=self.sha1)

    # --- diff patch ---

    def test_diff_download_allowed_for_permitted_user(self):
        response = self._get(
            DiffDownloadView,
            self.superuser,
            pk=self.device.pk,
            sha_old=self.sha1,
            sha_new=self.sha2,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'-set / interface ethernet-1/1 admin-state enable', response.content)

    def test_diff_download_denied_without_view_permission(self):
        with self.assertRaises(Http404):
            self._get(
                DiffDownloadView,
                self.plain_user,
                pk=self.device.pk,
                sha_old=self.sha1,
                sha_new=self.sha2,
            )


class TestDashboardView(TestCase):
    """
    The dashboard is served from the ConfigSnapshot index (no git access) and
    must only list devices the requesting user can view.
    """

    @classmethod
    def setUpTestData(cls):
        import datetime

        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='SR Linux', slug='sr-linux')
        role = DeviceRole.objects.create(name='Router', slug='router')
        cls.superuser = get_user_model().objects.create_superuser('dash-admin')
        cls.plain_user = get_user_model().objects.create_user('dash-nobody')
        # Repo path doesn't need to exist: the dashboard never opens git.
        cls.source = OxidizedSource.objects.create(name='Dash', git_repo_path='/tmp/does-not-exist-dash')
        for i, name in enumerate(('spine1', 'leaf1')):
            device = Device.objects.create(name=name, site=site, device_type=device_type, role=role)
            ConfigSnapshot.objects.create(
                device=device,
                source=cls.source,
                content=f'hostname {name}\n',
                commit_sha=str(i) * 40,
                commit_timestamp=datetime.datetime(2024, 1, 1 + i, tzinfo=datetime.UTC),
                commit_subject=f'backup {name}',
            )

    def _context_for(self, user):
        request = RequestFactory().get('/')
        request.user = user
        view = DashboardView()
        view.request = request
        return view.get_context_data()

    def test_lists_indexed_devices_newest_first(self):
        data = self._context_for(self.superuser)['devices_data']
        self.assertEqual([d['device'].name for d in data], ['leaf1', 'spine1'])
        leaf = data[0]
        self.assertEqual(leaf['filename'], 'leaf1')
        self.assertEqual(leaf['commit_subject'], 'backup leaf1')
        self.assertEqual(leaf['commit_sha'], '1' * 40)

    def test_unprivileged_user_sees_nothing(self):
        self.assertEqual(self._context_for(self.plain_user)['devices_data'], [])


class TestSearchViewPermissions(TestCase):
    """
    ConfigSearchView returns SearchHeadline excerpts of raw config content
    (SNMP communities, usernames), so it must restrict results to devices the
    requesting user can view — the highest-value leak surface in the plugin.
    """

    @classmethod
    def setUpTestData(cls):
        from core.models import ObjectType
        from users.models import ObjectPermission

        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='SR Linux', slug='sr-linux')
        role = DeviceRole.objects.create(name='Router', slug='router')
        cls.source = OxidizedSource.objects.create(name='Search', git_repo_path='/tmp/does-not-exist-search')
        cls.devices = {}
        for name in ('spine1', 'leaf1'):
            device = Device.objects.create(name=name, site=site, device_type=device_type, role=role)
            cls.devices[name] = device
            # Every config contains the term 'interface' — the search matches both.
            ConfigSnapshot.objects.create(
                device=device,
                source=cls.source,
                content=f'hostname {name}\nset / interface ethernet-1/1 admin-state enable\n',
                commit_sha=str(len(name)) * 40,
            )

        cls.superuser = get_user_model().objects.create_superuser('search-su')
        cls.plain_user = get_user_model().objects.create_user('search-nobody')

        # Constrained user: view permission on spine1 only.
        cls.constrained_user = get_user_model().objects.create_user('search-partial')
        perm = ObjectPermission.objects.create(
            name='view spine1 only', actions=['view'], constraints={'name': 'spine1'}
        )
        perm.object_types.add(ObjectType.objects.get_for_model(Device))
        perm.users.add(cls.constrained_user)

    def _results_for(self, user, query='interface'):
        request = RequestFactory().get('/search/', {'q': query})
        request.user = user
        view = ConfigSearchView()
        view.request = request
        return view.get_context_data()['results']

    def test_superuser_sees_all_matches(self):
        names = {r['device'].name for r in self._results_for(self.superuser)}
        self.assertEqual(names, {'spine1', 'leaf1'})

    def test_unprivileged_user_sees_nothing(self):
        self.assertEqual(self._results_for(self.plain_user), [])

    def test_constrained_user_sees_only_permitted_device(self):
        names = {r['device'].name for r in self._results_for(self.constrained_user)}
        self.assertEqual(names, {'spine1'})


class TestDashboardBackupHealth(TestCase):
    """The dashboard flags stale backups and lists active devices with no
    snapshot at all — the silent-backup-failure case."""

    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='SR Linux', slug='sr-linux')
        role = DeviceRole.objects.create(name='Router', slug='router')
        cls.superuser = get_user_model().objects.create_superuser('health-admin')
        cls.source = OxidizedSource.objects.create(name='Health', git_repo_path='/tmp/does-not-exist-health')

        def _device(name):
            return Device.objects.create(name=name, site=site, device_type=device_type, role=role, status='active')

        # Fresh backup (now) → ok.
        fresh = _device('fresh1')
        ConfigSnapshot.objects.create(
            device=fresh,
            source=cls.source,
            content='hostname fresh1\n',
            commit_sha='a' * 40,
            commit_timestamp=timezone.now(),
        )
        # Old backup (10 days) → stale (default threshold 26h).
        stale = _device('stale1')
        ConfigSnapshot.objects.create(
            device=stale,
            source=cls.source,
            content='hostname stale1\n',
            commit_sha='b' * 40,
            commit_timestamp=timezone.now() - datetime.timedelta(days=10),
        )
        # Active device, resolvable name, no snapshot → missing.
        _device('missing1')

    def _context(self):
        request = RequestFactory().get('/')
        request.user = self.superuser
        view = DashboardView()
        view.request = request
        return view.get_context_data()

    def test_counts(self):
        ctx = self._context()
        self.assertEqual(ctx['ok_count'], 1)
        self.assertEqual(ctx['stale_count'], 1)
        self.assertEqual(ctx['missing_count'], 1)

    def test_missing_device_listed(self):
        ctx = self._context()
        self.assertEqual([m['device'].name for m in ctx['missing']], ['missing1'])

    def test_stale_flag_on_row(self):
        ctx = self._context()
        by_name = {d['device'].name: d for d in ctx['devices_data']}
        self.assertFalse(by_name['fresh1']['is_stale'])
        self.assertTrue(by_name['stale1']['is_stale'])

    def test_out_of_scope_device_not_in_missing(self):
        # A server (out of scope) with no snapshot must NOT show as "never backed up".
        server_role = DeviceRole.objects.create(name='Server', slug='server')
        site = self.source.snapshots.first().device.site
        device_type = self.source.snapshots.first().device.device_type
        Device.objects.create(name='srv1', site=site, device_type=device_type, role=server_role, status='active')
        router_role = self.source.snapshots.first().device.role
        self.source.scope_roles.add(router_role)  # scope excludes the server role

        ctx = self._context()
        missing_names = [m['device'].name for m in ctx['missing']]
        self.assertIn('missing1', missing_names)  # in-scope router, still flagged
        self.assertNotIn('srv1', missing_names)  # out-of-scope server, suppressed


class TestSourceReindexView(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = OxidizedSource.objects.create(name='Reindex', git_repo_path='/tmp/does-not-exist-reindex')
        cls.superuser = get_user_model().objects.create_superuser('reindex-admin')
        cls.plain_user = get_user_model().objects.create_user('reindex-nobody')

    def _post(self, user):
        request = RequestFactory().post('/')
        request.user = user
        # RequestFactory bypasses message middleware; attach a dummy store.
        from django.contrib.messages.storage.fallback import FallbackStorage

        request.session = {}
        request._messages = FallbackStorage(request)
        return SourceReindexView.as_view()(request, pk=self.source.pk)

    @mock.patch('netbox_oxidized_viewer.jobs.ConfigSnapshotIndexJob.enqueue')
    def test_permitted_user_enqueues_and_redirects(self, mock_enqueue):
        mock_enqueue.return_value = mock.Mock(pk=42)
        response = self._post(self.superuser)
        self.assertEqual(response.status_code, 302)
        mock_enqueue.assert_called_once()

    @mock.patch('netbox_oxidized_viewer.jobs.ConfigSnapshotIndexJob.enqueue')
    def test_unprivileged_user_denied(self, mock_enqueue):
        with self.assertRaises(PermissionDenied):
            self._post(self.plain_user)
        mock_enqueue.assert_not_called()


def _request_with_messages(method='post', user=None, data=None):
    from django.contrib.messages.storage.fallback import FallbackStorage

    factory = RequestFactory()
    request = getattr(factory, method)('/', data or {})
    request.user = user
    request.session = {}
    request._messages = FallbackStorage(request)
    return request


class TestAddCommitNoteView(TestCase):
    @classmethod
    def setUpTestData(cls):
        from core.models import ObjectType
        from users.models import ObjectPermission

        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='SR Linux', slug='sr-linux')
        role = DeviceRole.objects.create(name='Router', slug='router')
        cls.device = Device.objects.create(
            name='spine1', site=site, device_type=device_type, role=role, status='active'
        )
        cls.sha = 'a' * 40
        cls.superuser = get_user_model().objects.create_superuser('note-ui-admin')
        # Device view but no add permission → reaches the perm check and is denied.
        cls.viewer = get_user_model().objects.create_user('note-ui-viewer')
        perm = ObjectPermission.objects.create(name='view dev', actions=['view'])
        perm.object_types.add(ObjectType.objects.get_for_model(Device))
        perm.users.add(cls.viewer)

    def test_superuser_adds_note_and_redirects(self):
        request = _request_with_messages(user=self.superuser, data={'message': 'reason for change'})
        response = AddCommitNoteView.as_view()(request, pk=self.device.pk, sha=self.sha)
        self.assertEqual(response.status_code, 302)
        note = ConfigCommitNote.objects.get(device=self.device, commit_sha=self.sha)
        self.assertEqual(note.message, 'reason for change')
        self.assertEqual(note.created_by, self.superuser)

    def test_viewer_without_add_perm_denied(self):
        request = _request_with_messages(user=self.viewer, data={'message': 'x'})
        with self.assertRaises(PermissionDenied):
            AddCommitNoteView.as_view()(request, pk=self.device.pk, sha=self.sha)
        self.assertFalse(ConfigCommitNote.objects.exists())


class TestDeviceSyncView(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='SR Linux', slug='sr-linux')
        role = DeviceRole.objects.create(name='Router', slug='router')
        cls.device = Device.objects.create(
            name='spine1', site=site, device_type=device_type, role=role, status='active'
        )
        cls.superuser = get_user_model().objects.create_superuser('sync-ui-admin')

    @mock.patch('netbox_oxidized_viewer.services.oxidized_api.trigger_backup')
    def test_sync_with_api_url_triggers(self, mock_trigger):
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo', api_url='http://oxi:8888')
        request = _request_with_messages(user=self.superuser)
        response = DeviceSyncView.as_view()(request, pk=self.device.pk)
        self.assertEqual(response.status_code, 302)
        mock_trigger.assert_called_once_with('http://oxi:8888', 'spine1')

    @mock.patch('netbox_oxidized_viewer.services.oxidized_api.trigger_backup')
    def test_sync_without_api_url_does_not_trigger(self, mock_trigger):
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo')
        request = _request_with_messages(user=self.superuser)
        response = DeviceSyncView.as_view()(request, pk=self.device.pk)
        self.assertEqual(response.status_code, 302)
        mock_trigger.assert_not_called()
