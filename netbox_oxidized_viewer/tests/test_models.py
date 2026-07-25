from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.core.exceptions import ValidationError
from django.test import TestCase

from netbox_oxidized_viewer.models import OxidizedSource


class OxidizedViewerModelsTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name='Test Site', slug='test-site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='SR Linux', slug='sr-linux')
        device_role = DeviceRole.objects.create(name='Router', slug='router')

        cls.device = Device.objects.create(
            name='spine1',
            site=site,
            device_type=device_type,
            role=device_role,
        )

    def test_oxidized_source_creation(self):
        source = OxidizedSource.objects.create(
            name='Lab Source',
            git_repo_path='/opt/oxidized-git',
        )
        self.assertEqual(source.name, 'Lab Source')
        self.assertEqual(source.node_name_source, 'name')
        self.assertEqual(str(source), 'Lab Source')

    def test_single_source_enforced(self):
        OxidizedSource.objects.create(name='First', git_repo_path='/opt/git')
        second = OxidizedSource(name='Second', git_repo_path='/opt/git2')
        with self.assertRaises(ValidationError):
            second.full_clean()

    def test_editing_existing_source_allowed(self):
        source = OxidizedSource.objects.create(name='Only', git_repo_path='/opt/git')
        # Re-validating/saving the existing row must not trip the guard.
        source.git_repo_path = '/opt/git-new'
        source.full_clean()  # should not raise
