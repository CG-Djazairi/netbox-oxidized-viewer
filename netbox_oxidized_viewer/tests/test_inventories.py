"""
Named inventories: one endpoint per Oxidized instance (zone), each exporting
only the devices in its own scope AND the source's scope, from the one shared
source/repository.
"""

import shutil
import tempfile

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.test import TestCase
from extras.models import Tag

from netbox_oxidized_viewer.models import OxidizedInventory, OxidizedSource

from .test_api import _grant_device_view
from .test_tasks import _build_repo


class TestNamedInventories(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_superuser('inv-admin', 'a@example.com', 'x')
        cls.viewer = get_user_model().objects.create_user('inv-viewer', password='x')
        _grant_device_view(cls.viewer)
        cls.nobody = get_user_model().objects.create_user('inv-nobody', password='x')

        cls.repo = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, cls.repo, ignore_errors=True)
        _build_repo(cls.repo, {'x1-sw1': 'hostname x1-sw1\n'})
        cls.source = OxidizedSource.objects.create(name='Central', git_repo_path=cls.repo)

        manufacturer = Manufacturer.objects.create(name='Cisco', slug='cisco')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='C9200L', slug='c9200l')
        cls.switch = DeviceRole.objects.create(name='Switch', slug='switch')
        cls.firewall = DeviceRole.objects.create(name='Firewall', slug='firewall')
        cls.x1 = Site.objects.create(name='Datacenter X1', slug='x1')
        cls.x3 = Site.objects.create(name='Datacenter X3', slug='x3')
        cls.oxi_tag = Tag.objects.create(name='oxidized', slug='oxidized')

        def dev(name, site, role, tags=()):
            d = Device.objects.create(name=name, site=site, device_type=device_type, role=role, status='active')
            d.tags.set(tags)
            return d

        dev('x1-sw1', cls.x1, cls.switch, [cls.oxi_tag])
        dev('x1-fw1', cls.x1, cls.firewall, [cls.oxi_tag])
        dev('x3-sw1', cls.x3, cls.switch, [cls.oxi_tag])
        dev('x3-sw2-untagged', cls.x3, cls.switch)

        cls.inv_x1 = OxidizedInventory.objects.create(name='Zone X1', slug='zone-x1')
        cls.inv_x1.scope_sites.set([cls.x1])
        cls.inv_x3_switches = OxidizedInventory.objects.create(name='Zone X3 switches', slug='zone-x3-sw')
        cls.inv_x3_switches.scope_sites.set([cls.x3])
        cls.inv_x3_switches.scope_roles.set([cls.switch])
        cls.inv_off = OxidizedInventory.objects.create(name='Off', slug='off', enabled=False)

    def _names(self, path, user=None):
        self.client.force_login(user or self.admin)
        response = self.client.get(path, HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200, (path, response.content[:300]))
        return sorted(e['name'] for e in response.json())

    def test_each_inventory_exports_only_its_scope(self):
        self.assertEqual(self._names('/api/plugins/oxidized-viewer/inventory/zone-x1/'), ['x1-fw1', 'x1-sw1'])
        self.assertEqual(
            self._names('/api/plugins/oxidized-viewer/inventory/zone-x3-sw/'), ['x3-sw1', 'x3-sw2-untagged']
        )

    def test_fleet_wide_inventory_unchanged(self):
        self.assertEqual(
            self._names('/api/plugins/oxidized-viewer/inventory/'), ['x1-fw1', 'x1-sw1', 'x3-sw1', 'x3-sw2-untagged']
        )

    def test_source_scope_still_applies(self):
        # The source's scope is the outer boundary: an inventory can narrow it, never widen it.
        self.source.scope_tags.set([self.oxi_tag])
        self.assertEqual(self._names('/api/plugins/oxidized-viewer/inventory/zone-x3-sw/'), ['x3-sw1'])

    def test_disabled_or_unknown_inventory_is_404(self):
        self.client.force_login(self.admin)
        for slug in ('off', 'does-not-exist'):
            response = self.client.get(
                f'/api/plugins/oxidized-viewer/inventory/{slug}/', HTTP_ACCEPT='application/json'
            )
            self.assertEqual(response.status_code, 404, slug)

    def test_token_permission_still_gates_the_export(self):
        self.client.force_login(self.nobody)
        response = self.client.get('/api/plugins/oxidized-viewer/inventory/zone-x1/', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            self._names('/api/plugins/oxidized-viewer/inventory/zone-x1/', user=self.viewer), ['x1-fw1', 'x1-sw1']
        )

    def test_api_root_lists_enabled_inventories(self):
        self.client.force_login(self.admin)
        body = self.client.get('/api/plugins/oxidized-viewer/', HTTP_ACCEPT='application/json').json()
        self.assertIn('inventory/zone-x1', body)
        self.assertIn('inventory/zone-x3-sw', body)
        self.assertNotIn('inventory/off', body)
        self.assertIn('inventories', body)

    def test_ui_create_and_page_shows_url_and_preview(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            '/plugins/oxidized-viewer/inventories/add/',
            {
                'name': 'Zone X3 all',
                'slug': 'zone-x3',
                'description': 'X3 ROP',
                'enabled': 'on',
                'scope_sites': [self.x3.pk],
            },
        )
        self.assertEqual(response.status_code, 302, response.content[:2000])
        inv = OxidizedInventory.objects.get(slug='zone-x3')
        self.assertEqual(response['Location'], inv.get_absolute_url())
        page = self.client.get(inv.get_absolute_url()).content.decode()
        self.assertIn('/api/plugins/oxidized-viewer/inventory/zone-x3/', page)
        self.assertIn('x3-sw1', page)
        self.assertNotIn('x1-sw1', page)
        self.assertEqual(self.client.get('/plugins/oxidized-viewer/inventories/').status_code, 200)
        self.assertEqual(self.client.get(f'/plugins/oxidized-viewer/inventories/{inv.pk}/changelog/').status_code, 200)

    def test_rest_crud_endpoint(self):
        self.client.force_login(self.admin)
        response = self.client.get('/api/plugins/oxidized-viewer/inventories/', HTTP_ACCEPT='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 3)
        response = self.client.post(
            '/api/plugins/oxidized-viewer/inventories/',
            {
                'name': 'Zone X1 firewalls',
                'slug': 'zone-x1-fw',
                'scope_sites': [self.x1.pk],
                'scope_roles': [self.firewall.pk],
            },
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 201, response.content[:500])
        self.assertEqual(self._names('/api/plugins/oxidized-viewer/inventory/zone-x1-fw/'), ['x1-fw1'])
