"""
List-style pages must not pull configuration bodies out of the database: they
show names, dates and commit ids. At fleet scale `content` is hundreds of MB.
"""

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import RequestFactory, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from netbox_oxidized_viewer.models import ConfigSnapshot, OxidizedSource
from netbox_oxidized_viewer.template_content import DeviceBackupStatus
from netbox_oxidized_viewer.views import DashboardView

TABLE = 'netbox_oxidized_viewer_configsnapshot'


def _snapshot_selects(queries):
    return [
        q['sql'] for q in queries if q['sql'].lstrip().upper().startswith('SELECT') and f'FROM "{TABLE}"' in q['sql']
    ]


class TestNoConfigBodyOnListPages(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Cisco', slug='cisco')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='C9200L', slug='c9200l')
        role = DeviceRole.objects.create(name='Switch', slug='switch')
        cls.admin = get_user_model().objects.create_superuser('weight-admin', 'a@example.com', 'x')
        cls.source = OxidizedSource.objects.create(name='Central', git_repo_path='/tmp/does-not-exist-weight')
        cls.device = Device.objects.create(name='sw1', site=site, device_type=device_type, role=role, status='active')
        ConfigSnapshot.objects.create(
            device=cls.device,
            source=cls.source,
            content='hostname sw1\n' * 1000,
            commit_sha='a' * 40,
            commit_timestamp=timezone.now(),
        )

    def _request(self):
        request = RequestFactory().get('/')
        request.user = self.admin
        return request

    def test_dashboard_does_not_select_content(self):
        view = DashboardView()
        view.request = self._request()
        with CaptureQueriesContext(connection) as ctx:
            context = view.get_context_data()
        self.assertEqual(len(context['devices_data']), 1)
        selects = _snapshot_selects(ctx.captured_queries)
        self.assertTrue(selects)
        for sql in selects:
            self.assertNotIn(f'"{TABLE}"."content"', sql)
            self.assertNotIn(f'"{TABLE}"."search_vector"', sql)

    def test_device_card_does_not_select_content(self):
        extension = DeviceBackupStatus(context={'object': self.device, 'request': self._request()})
        with CaptureQueriesContext(connection) as ctx:
            html = extension.right_page()
        self.assertIn('Config Backup', html)
        selects = _snapshot_selects(ctx.captured_queries)
        self.assertTrue(selects)
        for sql in selects:
            self.assertNotIn(f'"{TABLE}"."content"', sql)
            self.assertNotIn(f'"{TABLE}"."search_vector"', sql)
