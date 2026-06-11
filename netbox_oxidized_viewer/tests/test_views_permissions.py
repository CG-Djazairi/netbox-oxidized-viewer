"""
Object-level RBAC tests for the raw download views.

These are plain Django Views (not NetBox generic views), so they must apply
Device.objects.restrict() themselves — a user without view permission on a
device must get a 404 for its config, any historical commit, and any diff.
"""

import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.http import Http404
from django.test import TestCase, RequestFactory

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site

from netbox_oxidized_viewer.models import ConfigSnapshot, OxidizedSource
from netbox_oxidized_viewer.views import (
    CommitConfigDownloadView,
    DashboardView,
    DeviceConfigDownloadView,
    DiffDownloadView,
)

from .test_tasks import _build_repo


class TestDownloadViewPermissions(TestCase):

    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model='SR Linux', slug='sr-linux'
        )
        role = DeviceRole.objects.create(name='Router', slug='router')
        cls.device = Device.objects.create(
            name='spine1', site=site, device_type=device_type, role=role
        )
        cls.superuser = get_user_model().objects.create_superuser('dl-admin')
        cls.plain_user = get_user_model().objects.create_user('dl-nobody')

    def setUp(self):
        self.repo_path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo_path, ignore_errors=True)
        self.sha1 = _build_repo(self.repo_path, {
            'spine1': 'set / interface ethernet-1/1 admin-state enable\n',
        })
        self.sha2 = _build_repo(self.repo_path, {
            'spine1': 'set / interface ethernet-1/1 admin-state disable\n',
        }, parent=self.sha1, when=(2024, 2, 1))
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
        response = self._get(
            CommitConfigDownloadView, self.superuser, pk=self.device.pk, sha=self.sha1
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'admin-state enable', response.content)

    def test_commit_download_denied_without_view_permission(self):
        with self.assertRaises(Http404):
            self._get(
                CommitConfigDownloadView, self.plain_user, pk=self.device.pk, sha=self.sha1
            )

    # --- diff patch ---

    def test_diff_download_allowed_for_permitted_user(self):
        response = self._get(
            DiffDownloadView, self.superuser,
            pk=self.device.pk, sha_old=self.sha1, sha_new=self.sha2,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'-set / interface ethernet-1/1 admin-state enable', response.content)

    def test_diff_download_denied_without_view_permission(self):
        with self.assertRaises(Http404):
            self._get(
                DiffDownloadView, self.plain_user,
                pk=self.device.pk, sha_old=self.sha1, sha_new=self.sha2,
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
        device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model='SR Linux', slug='sr-linux'
        )
        role = DeviceRole.objects.create(name='Router', slug='router')
        cls.superuser = get_user_model().objects.create_superuser('dash-admin')
        cls.plain_user = get_user_model().objects.create_user('dash-nobody')
        # Repo path doesn't need to exist: the dashboard never opens git.
        cls.source = OxidizedSource.objects.create(
            name='Dash', git_repo_path='/tmp/does-not-exist-dash'
        )
        for i, name in enumerate(('spine1', 'leaf1')):
            device = Device.objects.create(
                name=name, site=site, device_type=device_type, role=role
            )
            ConfigSnapshot.objects.create(
                device=device,
                source=cls.source,
                content=f'hostname {name}\n',
                commit_sha=str(i) * 40,
                commit_timestamp=datetime.datetime(
                    2024, 1, 1 + i, tzinfo=datetime.timezone.utc
                ),
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
