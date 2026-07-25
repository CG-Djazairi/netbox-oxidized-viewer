"""
Tests for settings-based auto-provisioning of the OxidizedSource.

The post_migrate receiver is guarded against test runs, so these call the
underlying provision_source_from_settings() directly with override_settings.
"""

from django.test import TestCase, override_settings

from netbox_oxidized_viewer.bootstrap import provision_source_from_settings
from netbox_oxidized_viewer.models import OxidizedSource


class TestProvisionFromSettings(TestCase):

    @override_settings(PLUGINS_CONFIG={'netbox_oxidized_viewer': {
        'git_repo_path': '/opt/oxidized-git',
        'source_name': 'auto',
        'node_name_source': 'serial',
        'api_url': 'http://oxidized:8888',
    }})
    def test_creates_source_when_configured(self):
        created = provision_source_from_settings()
        self.assertIsNotNone(created)
        s = OxidizedSource.objects.get()
        self.assertEqual(s.name, 'auto')
        self.assertEqual(s.git_repo_path, '/opt/oxidized-git')
        self.assertEqual(s.node_name_source, 'serial')
        self.assertEqual(s.api_url, 'http://oxidized:8888')

    @override_settings(PLUGINS_CONFIG={'netbox_oxidized_viewer': {'git_repo_path': '/opt/oxidized-git'}})
    def test_defaults_applied(self):
        provision_source_from_settings()
        s = OxidizedSource.objects.get()
        self.assertEqual(s.name, 'default')          # default source_name
        self.assertEqual(s.node_name_source, 'name')  # default mapping

    @override_settings(PLUGINS_CONFIG={'netbox_oxidized_viewer': {'git_repo_path': '/opt/oxidized-git'}})
    def test_does_not_clobber_existing_source(self):
        OxidizedSource.objects.create(name='ui-made', git_repo_path='/existing/path')
        result = provision_source_from_settings()
        self.assertIsNone(result)
        self.assertEqual(OxidizedSource.objects.count(), 1)
        # UI-created source is left exactly as it was.
        self.assertEqual(OxidizedSource.objects.get().git_repo_path, '/existing/path')

    @override_settings(PLUGINS_CONFIG={'netbox_oxidized_viewer': {}})
    def test_noop_when_git_repo_path_unset(self):
        result = provision_source_from_settings()
        self.assertIsNone(result)
        self.assertEqual(OxidizedSource.objects.count(), 0)
