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

from core.models import ObjectChange
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
