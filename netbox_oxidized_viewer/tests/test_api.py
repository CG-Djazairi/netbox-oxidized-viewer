"""
Tests for the Oxidized inventory API endpoint (OxidizedInventoryView), which
returns the active-device list in Oxidized's HTTP source format.
"""

import shutil
import tempfile
from unittest import mock

from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Platform, Site
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIRequestFactory, force_authenticate
from users.models import ObjectPermission

from netbox_oxidized_viewer.api.views import (
    DeviceCommitNoteAPIView,
    DeviceConfigAPIView,
    DeviceDiffAPIView,
    DeviceHistoryAPIView,
    DeviceSyncAPIView,
    OxidizedInventoryView,
)
from netbox_oxidized_viewer.models import ConfigCommitNote, OxidizedSource

from .test_tasks import _build_repo


def _grant(user, model, actions, constraints=None):
    perm = ObjectPermission.objects.create(
        name=f'{"-".join(actions)}-{model.__name__}-{user.username}',
        actions=actions,
        constraints=constraints,
    )
    perm.users.add(user)
    perm.object_types.add(ObjectType.objects.get_for_model(model))
    return perm


def _grant_device_view(user, constraints=None):
    """Give `user` a NetBox ObjectPermission to view devices."""
    perm = ObjectPermission.objects.create(
        name=f'view-devices-{user.username}',
        actions=['view'],
        constraints=constraints,
    )
    perm.users.add(user)
    perm.object_types.add(ObjectType.objects.get_for_model(Device))
    return perm


class TestOxidizedInventoryView(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user('tester', password='x')
        _grant_device_view(cls.user)
        cls.unprivileged = get_user_model().objects.create_user('nobody', password='x')
        cls.site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        cls.device_type = DeviceType.objects.create(manufacturer=manufacturer, model='SR Linux', slug='sr-linux')
        cls.role = DeviceRole.objects.create(name='Router', slug='router')

    def _get(self, authenticate=True, user=None):
        request = APIRequestFactory().get('/api/plugins/oxidized-viewer/source/')
        if authenticate:
            force_authenticate(request, user=user or self.user)
        return OxidizedInventoryView.as_view()(request)

    def test_requires_authentication(self):
        response = self._get(authenticate=False)
        self.assertIn(response.status_code, (401, 403))

    def test_requires_device_view_permission(self):
        response = self._get(user=self.unprivileged)
        self.assertEqual(response.status_code, 403)

    def test_constrained_permission_limits_inventory(self):
        # A token scoped to a subset of devices must only export that subset.
        constrained = get_user_model().objects.create_user('scoped', password='x')
        _grant_device_view(constrained, constraints={'name': 'spine1'})
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo')
        for name in ('spine1', 'leaf1'):
            Device.objects.create(
                name=name,
                site=self.site,
                device_type=self.device_type,
                role=self.role,
                status='active',
            )
        response = self._get(user=constrained)
        self.assertEqual(response.status_code, 200)
        self.assertEqual({e['name'] for e in response.data}, {'spine1'})

    def test_empty_when_no_source(self):
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, [])

    def test_returns_active_devices(self):
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo')
        Device.objects.create(
            name='spine1',
            site=self.site,
            device_type=self.device_type,
            role=self.role,
            status='active',
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        entry = response.data[0]
        self.assertEqual(entry['name'], 'spine1')
        self.assertEqual(entry['model'], 'sr linux')  # device_type.model.lower()
        self.assertEqual(entry['ip'], '')  # no primary_ip4 assigned

    def test_model_uses_platform_slug_when_set(self):
        # Oxidized's model field is a driver name → NetBox platform, not the
        # hardware model. A device with a platform exports its slug.
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo')
        platform = Platform.objects.create(name='Arista EOS', slug='eos')
        Device.objects.create(
            name='spine1',
            site=self.site,
            device_type=self.device_type,
            role=self.role,
            platform=platform,
            status='active',
        )
        entry = self._get().data[0]
        self.assertEqual(entry['model'], 'eos')

    @override_settings(PLUGINS_CONFIG={'netbox_oxidized_viewer': {'platform_model_map': {'cisco-ios-xe': 'ios'}}})
    def test_platform_model_map_overrides_slug(self):
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo')
        platform = Platform.objects.create(name='Cisco IOS XE', slug='cisco-ios-xe')
        Device.objects.create(
            name='rtr1',
            site=self.site,
            device_type=self.device_type,
            role=self.role,
            platform=platform,
            status='active',
        )
        entry = self._get().data[0]
        self.assertEqual(entry['model'], 'ios')

    @override_settings(PLUGINS_CONFIG={'netbox_oxidized_viewer': {'inventory_group_field': 'site'}})
    def test_group_field_exported_when_configured(self):
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo')
        Device.objects.create(
            name='spine1',
            site=self.site,
            device_type=self.device_type,
            role=self.role,
            status='active',
        )
        entry = self._get().data[0]
        self.assertEqual(entry['group'], 'Site')

    def test_no_group_field_by_default(self):
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo')
        Device.objects.create(
            name='spine1',
            site=self.site,
            device_type=self.device_type,
            role=self.role,
            status='active',
        )
        self.assertNotIn('group', self._get().data[0])

    def test_excludes_inactive_devices(self):
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo')
        Device.objects.create(
            name='active-dev',
            site=self.site,
            device_type=self.device_type,
            role=self.role,
            status='active',
        )
        Device.objects.create(
            name='offline-dev',
            site=self.site,
            device_type=self.device_type,
            role=self.role,
            status='offline',
        )
        response = self._get()
        names = {e['name'] for e in response.data}
        self.assertEqual(names, {'active-dev'})

    def test_scope_roles_limits_inventory(self):
        # Out-of-scope roles (e.g. servers, passives) must not be exported, so
        # Oxidized never polls them.
        source = OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo')
        server_role = DeviceRole.objects.create(name='Server', slug='server')
        Device.objects.create(
            name='rtr1',
            site=self.site,
            device_type=self.device_type,
            role=self.role,
            status='active',
        )
        Device.objects.create(
            name='srv1',
            site=self.site,
            device_type=self.device_type,
            role=server_role,
            status='active',
        )
        source.scope_roles.add(self.role)  # only the Router role is in scope
        names = {e['name'] for e in self._get().data}
        self.assertEqual(names, {'rtr1'})

    def test_empty_scope_exports_all_active(self):
        # Backward-compatible: no scope configured → every active device.
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo')
        other = DeviceRole.objects.create(name='Server', slug='server')
        Device.objects.create(
            name='rtr1', site=self.site, device_type=self.device_type, role=self.role, status='active'
        )
        Device.objects.create(name='srv1', site=self.site, device_type=self.device_type, role=other, status='active')
        names = {e['name'] for e in self._get().data}
        self.assertEqual(names, {'rtr1', 'srv1'})


class TestDeviceConfigAPI(TestCase):
    """Read-only config/history/diff API — same object-level RBAC as the UI."""

    @classmethod
    def setUpTestData(cls):
        cls.superuser = get_user_model().objects.create_superuser('cfg-admin')
        cls.plain_user = get_user_model().objects.create_user('cfg-nobody')
        cls.site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        cls.device_type = DeviceType.objects.create(manufacturer=manufacturer, model='SR Linux', slug='sr-linux')
        cls.role = DeviceRole.objects.create(name='Router', slug='router')
        cls.device = Device.objects.create(
            name='spine1',
            site=cls.site,
            device_type=cls.device_type,
            role=cls.role,
            status='active',
        )

    def setUp(self):
        self.repo_path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo_path, ignore_errors=True)
        self.sha1 = _build_repo(self.repo_path, {'spine1': 'admin-state enable\n'})
        self.sha2 = _build_repo(
            self.repo_path,
            {'spine1': 'admin-state disable\n'},
            parent=self.sha1,
            when=(2024, 2, 1),
        )
        self.source = OxidizedSource.objects.create(name='Lab', git_repo_path=self.repo_path)

    def _get(self, view_cls, user, **kwargs):
        request = APIRequestFactory().get('/')
        force_authenticate(request, user=user)
        return view_cls.as_view()(request, **kwargs)

    def test_config_latest(self):
        resp = self._get(DeviceConfigAPIView, self.superuser, pk=self.device.pk)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['commit_sha'], self.sha2)
        self.assertIn('disable', resp.data['content'])

    def test_config_by_sha(self):
        request = APIRequestFactory().get('/', {'sha': self.sha1})
        force_authenticate(request, user=self.superuser)
        resp = DeviceConfigAPIView.as_view()(request, pk=self.device.pk)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('enable', resp.data['content'])

    def test_config_denied_without_permission(self):
        resp = self._get(DeviceConfigAPIView, self.plain_user, pk=self.device.pk)
        self.assertEqual(resp.status_code, 404)

    def test_history(self):
        resp = self._get(DeviceHistoryAPIView, self.superuser, pk=self.device.pk)
        self.assertEqual(resp.status_code, 200)
        shas = [c['sha'] for c in resp.data['commits']]
        self.assertEqual(shas, [self.sha2, self.sha1])

    def test_history_denied_without_permission(self):
        resp = self._get(DeviceHistoryAPIView, self.plain_user, pk=self.device.pk)
        self.assertEqual(resp.status_code, 404)

    def test_diff(self):
        resp = self._get(
            DeviceDiffAPIView,
            self.superuser,
            pk=self.device.pk,
            sha_old=self.sha1,
            sha_new=self.sha2,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['old_sha'], self.sha1)
        markers = [ln['marker'] for h in resp.data['hunks'] for ln in h['lines']]
        self.assertIn('-', markers)
        self.assertIn('+', markers)

    def test_diff_denied_without_permission(self):
        resp = self._get(
            DeviceDiffAPIView,
            self.plain_user,
            pk=self.device.pk,
            sha_old=self.sha1,
            sha_new=self.sha2,
        )
        self.assertEqual(resp.status_code, 404)


class TestCommitNoteAPI(TestCase):
    """POST/GET commit notes with layered RBAC (device view + add perm)."""

    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='SR Linux', slug='sr-linux')
        role = DeviceRole.objects.create(name='Router', slug='router')
        cls.device = Device.objects.create(
            name='spine1', site=site, device_type=device_type, role=role, status='active'
        )
        cls.sha = 'a' * 40
        cls.superuser = get_user_model().objects.create_superuser('note-admin')
        cls.plain_user = get_user_model().objects.create_user('note-nobody')
        # Device view but no add permission.
        cls.viewer = get_user_model().objects.create_user('note-viewer')
        _grant(cls.viewer, Device, ['view'])
        # Device view + note-add (the automation-token path).
        cls.author = get_user_model().objects.create_user('note-author')
        _grant(cls.author, Device, ['view'])
        _grant(cls.author, ConfigCommitNote, ['add'])

    def _post(self, user, message):
        request = APIRequestFactory().post('/', {'message': message}, format='json')
        force_authenticate(request, user=user)
        return DeviceCommitNoteAPIView.as_view()(request, pk=self.device.pk, sha=self.sha)

    def test_author_creates_note(self):
        resp = self._post(self.author, 'change #4211: bump MTU')
        self.assertEqual(resp.status_code, 201)
        self.assertTrue(ConfigCommitNote.objects.filter(device=self.device, commit_sha=self.sha).exists())

    def test_get_lists_notes(self):
        ConfigCommitNote.objects.create(device=self.device, commit_sha=self.sha, message='hi')
        request = APIRequestFactory().get('/')
        force_authenticate(request, user=self.superuser)
        resp = DeviceCommitNoteAPIView.as_view()(request, pk=self.device.pk, sha=self.sha)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]['message'], 'hi')

    def test_denied_without_device_view(self):
        resp = self._post(self.plain_user, 'x')
        self.assertEqual(resp.status_code, 404)
        self.assertFalse(ConfigCommitNote.objects.exists())

    def test_denied_without_add_permission(self):
        resp = self._post(self.viewer, 'x')
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(ConfigCommitNote.objects.exists())

    def test_empty_message_rejected(self):
        resp = self._post(self.superuser, '   ')
        self.assertEqual(resp.status_code, 400)


class TestDeviceSyncAPI(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='SR Linux', slug='sr-linux')
        role = DeviceRole.objects.create(name='Router', slug='router')
        cls.device = Device.objects.create(
            name='spine1', site=site, device_type=device_type, role=role, status='active'
        )
        cls.superuser = get_user_model().objects.create_superuser('sync-admin')

    def _post(self):
        request = APIRequestFactory().post('/')
        force_authenticate(request, user=self.superuser)
        return DeviceSyncAPIView.as_view()(request, pk=self.device.pk)

    @mock.patch('netbox_oxidized_viewer.services.oxidized_api.trigger_backup')
    def test_sync_triggers_backup(self, mock_trigger):
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo', api_url='http://oxi:8888')
        resp = self._post()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['node'], 'spine1')
        mock_trigger.assert_called_once_with('http://oxi:8888', 'spine1')

    @mock.patch('netbox_oxidized_viewer.services.oxidized_api.trigger_backup')
    def test_sync_without_api_url_is_400(self, mock_trigger):
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo')  # no api_url
        resp = self._post()
        self.assertEqual(resp.status_code, 400)
        mock_trigger.assert_not_called()

    @mock.patch('netbox_oxidized_viewer.services.oxidized_api.trigger_backup')
    def test_sync_requires_change_permission(self, mock_trigger):
        # Triggering a backup is a write on Oxidized: device *view* is not enough.
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo', api_url='http://oxi:8888')
        viewer = get_user_model().objects.create_user('sync-viewer')
        _grant_device_view(viewer)
        request = APIRequestFactory().post('/')
        force_authenticate(request, user=viewer)
        resp = DeviceSyncAPIView.as_view()(request, pk=self.device.pk)
        self.assertEqual(resp.status_code, 404)
        mock_trigger.assert_not_called()

        changer = get_user_model().objects.create_user('sync-changer')
        _grant(changer, Device, ['view', 'change'])
        request = APIRequestFactory().post('/')
        force_authenticate(request, user=changer)
        resp = DeviceSyncAPIView.as_view()(request, pk=self.device.pk)
        self.assertEqual(resp.status_code, 200)
        mock_trigger.assert_called_once()


class TestInventoryIpField(TestCase):
    """The `ip` exported by the inventory endpoint follows the source's inventory_ip_field."""

    @classmethod
    def setUpTestData(cls):
        from core.models import ObjectType as _ObjectType
        from extras.models import CustomField
        from ipam.models import IPAddress

        cls.user = get_user_model().objects.create_user('tester', password='x')
        _grant_device_view(cls.user)
        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='SR Linux', slug='sr-linux')
        role = DeviceRole.objects.create(name='Router', slug='router')
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo')

        cf = CustomField.objects.create(
            name='management_interface',
            type='object',
            related_object_type=_ObjectType.objects.get_for_model(IPAddress),
        )
        cf.object_types.set([_ObjectType.objects.get_for_model(Device)])
        mgmt_ip = IPAddress.objects.create(address='192.0.2.10/24')
        primary_ip = IPAddress.objects.create(address='198.51.100.1/32')
        cls.device = Device.objects.create(
            name='spine1',
            site=site,
            device_type=device_type,
            role=role,
            status='active',
            custom_field_data={'management_interface': mgmt_ip.pk},
        )
        cls.device.primary_ip4 = primary_ip
        cls.device.save()
        Device.objects.create(name='leaf1', site=site, device_type=device_type, role=role, status='active')

    def _get(self):
        request = APIRequestFactory().get('/api/plugins/oxidized-viewer/source/')
        force_authenticate(request, user=self.user)
        return {e['name']: e for e in OxidizedInventoryView.as_view()(request).data}

    def test_default_is_primary_ipv4(self):
        entries = self._get()
        self.assertEqual(entries['spine1']['ip'], '198.51.100.1')
        self.assertEqual(entries['leaf1']['ip'], '')

    def test_object_custom_field_exports_bare_address(self):
        OxidizedSource.objects.update(inventory_ip_field='cf_management_interface')
        entries = self._get()
        self.assertEqual(entries['spine1']['ip'], '192.0.2.10')
        self.assertEqual(entries['leaf1']['ip'], '')
