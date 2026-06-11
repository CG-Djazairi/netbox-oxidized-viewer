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

from netbox_oxidized_viewer.models import OxidizedSource
from netbox_oxidized_viewer.views import (
    CommitConfigDownloadView,
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
