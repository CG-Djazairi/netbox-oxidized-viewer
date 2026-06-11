"""
Tests for the Oxidized inventory API endpoint (OxidizedInventoryView), which
returns the active-device list in Oxidized's HTTP source format.
"""

from core.models import ObjectType
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIRequestFactory, force_authenticate
from users.models import ObjectPermission

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site

from netbox_oxidized_viewer.models import OxidizedSource
from netbox_oxidized_viewer.api.views import OxidizedInventoryView


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
        cls.device_type = DeviceType.objects.create(
            manufacturer=manufacturer, model='SR Linux', slug='sr-linux'
        )
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
                name=name, site=self.site, device_type=self.device_type,
                role=self.role, status='active',
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
            name='spine1', site=self.site, device_type=self.device_type,
            role=self.role, status='active',
        )
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        entry = response.data[0]
        self.assertEqual(entry['name'], 'spine1')
        self.assertEqual(entry['model'], 'sr linux')  # device_type.model.lower()
        self.assertEqual(entry['ip'], '')               # no primary_ip4 assigned

    def test_excludes_inactive_devices(self):
        OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo')
        Device.objects.create(
            name='active-dev', site=self.site, device_type=self.device_type,
            role=self.role, status='active',
        )
        Device.objects.create(
            name='offline-dev', site=self.site, device_type=self.device_type,
            role=self.role, status='offline',
        )
        response = self._get()
        names = {e['name'] for e in response.data}
        self.assertEqual(names, {'active-dev'})
