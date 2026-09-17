"""
Regression tests for saving an OxidizedSource.

OxidizedSource is a NetBoxModel, so a UI save runs through NetBox's change-log /
event-rule pipeline, which serializes the object with the model's REST API
serializer. Before ``api/serializers.py`` existed this raised
``SerializerNotFound`` on every save. These tests exercise the real views
through the test client (middleware included) so the pipeline actually runs.
"""

import shutil
import tempfile
from unittest import mock

from core.models import Job, ObjectChange
from django.contrib.auth import get_user_model
from django.test import TestCase

from netbox_oxidized_viewer.models import OxidizedSource

from .test_tasks import _build_repo


class TestOxidizedSourceSave(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('admin', 'admin@example.com', 'x')
        cls.repo = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.repo, ignore_errors=True)
        _build_repo(cls.repo, {'spine1': 'hostname spine1\n'})

    def setUp(self):
        self.client.force_login(self.admin)

    def _post(self, url, **overrides):
        data = {'name': 'Lab', 'git_repo_path': self.repo, 'node_name_source': 'name', 'api_url': ''}
        data.update(overrides)
        return self.client.post(url, data)

    def test_ui_create_saves_and_logs_change(self):
        response = self._post('/plugins/oxidized-viewer/sources/add/')
        self.assertEqual(response.status_code, 302, response.content[:2000])
        source = OxidizedSource.objects.get(name='Lab')
        self.assertTrue(ObjectChange.objects.filter(changed_object_id=source.pk).exists())

    def test_ui_edit_saves(self):
        source = OxidizedSource.objects.create(name='Lab', git_repo_path=self.repo)
        response = self._post(f'/plugins/oxidized-viewer/sources/{source.pk}/edit/', name='Renamed')
        self.assertEqual(response.status_code, 302, response.content[:2000])
        source.refresh_from_db()
        self.assertEqual(source.name, 'Renamed')

    def test_rest_endpoint_list_and_detail(self):
        source = OxidizedSource.objects.create(name='Lab', git_repo_path=self.repo)
        response = self.client.get('/api/plugins/oxidized-viewer/sources/', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200, response.content[:500])
        body = response.json()
        self.assertEqual(body['count'], 1)
        self.assertEqual(body['results'][0]['name'], 'Lab')
        self.assertIn('url', body['results'][0])

        response = self.client.get(f'/api/plugins/oxidized-viewer/sources/{source.pk}/', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['git_repo_path'], self.repo)

    def test_edit_from_list_returns_to_the_source_page(self):
        source = OxidizedSource.objects.create(name='Lab', git_repo_path=self.repo)
        response = self._post(
            f'/plugins/oxidized-viewer/sources/{source.pk}/edit/?return_url=/plugins/oxidized-viewer/sources/',
            name='Renamed',
        )
        self.assertEqual(response.status_code, 302, response.content[:2000])
        self.assertEqual(response['Location'], source.get_absolute_url())

    def test_ip_field_editable_and_shown(self):
        source = OxidizedSource.objects.create(name='Lab', git_repo_path=self.repo)
        response = self._post(
            f'/plugins/oxidized-viewer/sources/{source.pk}/edit/', inventory_ip_field='cf_management_interface'
        )
        self.assertEqual(response.status_code, 302, response.content[:2000])
        source.refresh_from_db()
        self.assertEqual(source.inventory_ip_field, 'cf_management_interface')
        page = self.client.get(source.get_absolute_url()).content.decode()
        self.assertIn('cf_management_interface', page)
        api = self.client.get(f'/api/plugins/oxidized-viewer/sources/{source.pk}/', HTTP_ACCEPT='application/json')
        self.assertEqual(api.json()['inventory_ip_field'], 'cf_management_interface')

    @mock.patch('django_rq.get_queue')
    def test_reindex_button_enqueues_job_attached_to_source(self, mock_get_queue):
        # Regression: OxidizedSource lacked the 'jobs' feature, so Job.clean()
        # rejected the attachment and the button 500ed.
        source = OxidizedSource.objects.create(name='Lab', git_repo_path=self.repo)
        response = self.client.post(f'/plugins/oxidized-viewer/sources/{source.pk}/reindex/')
        self.assertEqual(response.status_code, 302, response.content[:2000])
        job = Job.objects.get(object_id=source.pk, name='Update Oxidized config snapshots')
        self.assertEqual(job.object, source)
        mock_get_queue.assert_called()  # handed to RQ (the exact queue call is NetBox's business)

    def test_source_page_has_jobs_tab(self):
        source = OxidizedSource.objects.create(name='Lab', git_repo_path=self.repo)
        response = self.client.get(f'/plugins/oxidized-viewer/sources/{source.pk}/jobs/')
        self.assertEqual(response.status_code, 200)

    def test_api_root_lists_every_endpoint(self):
        response = self.client.get('/api/plugins/oxidized-viewer/', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        for key in (
            'sources',
            'inventory',
            'source',
            'devices/{pk}/config',
            'devices/{pk}/history',
            'devices/{pk}/sync',
        ):
            self.assertIn(key, body)
        self.assertTrue(body['inventory'].endswith('/api/plugins/oxidized-viewer/inventory/'))

    def test_inventory_alias_and_legacy_path(self):
        OxidizedSource.objects.create(name='Lab', git_repo_path=self.repo)
        for path in ('/api/plugins/oxidized-viewer/inventory/', '/api/plugins/oxidized-viewer/source/'):
            response = self.client.get(path, HTTP_ACCEPT='application/json')
            self.assertEqual(response.status_code, 200, path)
            self.assertIsInstance(response.json(), list)

    def test_malformed_sha_is_404_not_500(self):
        from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site

        OxidizedSource.objects.create(name='Lab', git_repo_path=self.repo)
        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='SR Linux', slug='sr-linux')
        role = DeviceRole.objects.create(name='Router', slug='router')
        device = Device.objects.create(name='spine1', site=site, device_type=device_type, role=role)
        bad = 'not-a-sha-' + 'z' * 40
        for path in (
            f'/plugins/oxidized-viewer/devices/{device.pk}/commit/{bad}/',
            f'/plugins/oxidized-viewer/devices/{device.pk}/diff/{bad}/{bad}/',
            f'/api/plugins/oxidized-viewer/devices/{device.pk}/diff/{bad}/{bad}/',
        ):
            self.assertEqual(self.client.get(path).status_code, 404, path)
        response = self.client.post(
            f'/api/plugins/oxidized-viewer/devices/{device.pk}/commits/{bad}/note/', {'message': 'x'}
        )
        self.assertEqual(response.status_code, 404)
