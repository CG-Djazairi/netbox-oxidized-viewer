"""
Unit tests for netbox_oxidized_viewer.utils:
  resolve_device_field  — device field expression resolution
  get_source            — single-source lookup helper
"""

from types import SimpleNamespace

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.test import TestCase

from netbox_oxidized_viewer.models import OxidizedSource
from netbox_oxidized_viewer.utils import get_source, resolve_device_field


class TestResolveDeviceField(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='SR Linux', slug='sr-linux')
        role = DeviceRole.objects.create(name='Router', slug='router')
        cls.device = Device.objects.create(
            name='spine1',
            site=site,
            device_type=device_type,
            role=role,
            serial='SN-123',
            asset_tag='ASSET-9',
        )
        cls.device.custom_field_data = {'oxidized_name': 'spine1-cfg'}

    def test_resolve_name(self):
        self.assertEqual(resolve_device_field(self.device, 'name'), 'spine1')

    def test_resolve_serial(self):
        self.assertEqual(resolve_device_field(self.device, 'serial'), 'SN-123')

    def test_resolve_asset_tag(self):
        self.assertEqual(resolve_device_field(self.device, 'asset_tag'), 'ASSET-9')

    def test_resolve_custom_field(self):
        self.assertEqual(resolve_device_field(self.device, 'cf_oxidized_name'), 'spine1-cfg')

    def test_resolve_missing_custom_field_returns_none(self):
        self.assertIsNone(resolve_device_field(self.device, 'cf_does_not_exist'))

    def test_resolve_empty_expression_returns_none(self):
        self.assertIsNone(resolve_device_field(self.device, ''))

    def test_resolve_ip_proxy_object(self):
        # IP address fields expose .address.ip — the helper must unwrap them.
        self.device.fake_ip = SimpleNamespace(address=SimpleNamespace(ip='10.0.0.1'))
        self.assertEqual(resolve_device_field(self.device, 'fake_ip'), '10.0.0.1')


class TestGetSource(TestCase):
    def test_returns_none_when_no_source(self):
        self.assertIsNone(get_source())

    def test_returns_source_when_present(self):
        source = OxidizedSource.objects.create(name='Lab', git_repo_path='/tmp/repo')
        self.assertEqual(get_source(), source)
