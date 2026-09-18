"""
Backup health: a device that is backed up successfully but never changes must
not be reported as stale. Commit age = last change; BackupStatus = last run.
"""

import datetime
from unittest import mock

from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.utils import timezone
from users.models import ObjectPermission

from netbox_oxidized_viewer import health
from netbox_oxidized_viewer.models import BackupStatus, ConfigSnapshot, OxidizedSource
from netbox_oxidized_viewer.views import DashboardView

HOOK = '/api/plugins/oxidized-viewer/hook/'


class TestBackupHealth(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name='Site', slug='site')
        manufacturer = Manufacturer.objects.create(name='Cisco', slug='cisco')
        device_type = DeviceType.objects.create(manufacturer=manufacturer, model='C9200L', slug='c9200l')
        role = DeviceRole.objects.create(name='Switch', slug='switch')
        cls.admin = get_user_model().objects.create_superuser('health-admin2', 'a@example.com', 'x')
        cls.source = OxidizedSource.objects.create(name='Central', git_repo_path='/tmp/does-not-exist-health2')
        cls.old = timezone.now() - datetime.timedelta(days=30)

        def dev(name, snapshot=True):
            d = Device.objects.create(name=name, site=site, device_type=device_type, role=role, status='active')
            if snapshot:
                ConfigSnapshot.objects.create(
                    device=d,
                    source=cls.source,
                    content=f'hostname {name}\n',
                    commit_sha='a' * 40,
                    commit_timestamp=cls.old,
                )
            return d

        cls.quiet = dev('quiet-switch')  # config unchanged for 30 days
        cls.broken = dev('broken-switch')
        cls.gone = dev('gone-switch')
        cls.never = dev('never-backed-up', snapshot=False)

    # ---- pure logic ----
    def test_unchanged_but_successfully_polled_device_is_ok(self):
        snap = self.quiet.oxidized_snapshot
        self.assertEqual(health.compute_health(snap, None, 26), health.UNVERIFIED)
        status = health.record_run(self.quiet, 'success')
        self.assertEqual(health.compute_health(snap, status, 26), health.OK)

    def test_failed_run_is_failing_with_reason(self):
        status = health.record_run(self.broken, 'no_connection', error='Timeout::Error: execution expired')
        self.assertEqual(health.compute_health(self.broken.oxidized_snapshot, status, 26), health.FAILING)
        self.assertEqual(status.last_error, 'Timeout::Error: execution expired')
        self.assertIsNone(status.last_success)

    def test_runs_that_stopped_are_stale(self):
        long_ago = timezone.now() - datetime.timedelta(days=3)
        status = health.record_run(self.gone, 'success', when=long_ago)
        self.assertEqual(health.compute_health(self.gone.oxidized_snapshot, status, 26), health.STALE)

    def test_older_report_never_overwrites_newer(self):
        health.record_run(self.quiet, 'success')
        health.record_run(self.quiet, 'timeout', when=timezone.now() - datetime.timedelta(hours=5))
        self.assertEqual(BackupStatus.objects.get(device=self.quiet).last_status, 'success')

    # ---- dashboard ----
    def test_dashboard_counts_use_run_reports(self):
        health.record_run(self.quiet, 'success')
        health.record_run(self.broken, 'no_connection', error='boom')
        health.record_run(self.never, 'timeout', error='Timeout::Error: execution expired')
        request = RequestFactory().get('/')
        request.user = self.admin
        view = DashboardView()
        view.request = request
        ctx = view.get_context_data()
        self.assertEqual((ctx['ok_count'], ctx['failing_count'], ctx['unverified_count']), (1, 1, 1))
        self.assertTrue(ctx['has_run_reports'])
        missing = {m['device'].name: m for m in ctx['missing']}
        self.assertEqual(missing['never-backed-up']['status'].last_status, 'timeout')

    # ---- hook endpoint ----
    def test_hook_success_and_failure(self):
        self.client.force_login(self.admin)
        r = self.client.post(HOOK, {'event': 'node_success', 'node': 'quiet-switch', 'status': 'success'})
        self.assertEqual(r.status_code, 201, r.content[:300])
        self.assertEqual(BackupStatus.objects.get(device=self.quiet).last_status, 'success')

        r = self.client.post(
            HOOK,
            {
                'event': 'node_fail',
                'node': 'broken-switch',
                'status': 'no_connection',
                'err_type': 'Timeout::Error',
                'err_reason': 'execution expired',
            },
        )
        self.assertEqual(r.status_code, 201, r.content[:300])
        status = BackupStatus.objects.get(device=self.broken)
        self.assertEqual(
            (status.last_status, status.last_error), ('no_connection', 'Timeout::Error: execution expired')
        )

    def test_hook_accepts_urlencoded_and_json_bodies(self):
        # The documented hook uses curl --data-urlencode (application/x-www-form-urlencoded).
        self.client.force_login(self.admin)
        r = self.client.post(
            HOOK,
            'event=node_fail&node=broken-switch&status=timeout&err_type=Timeout%3A%3AError&err_reason=it%20said%20%22no%22',
            content_type='application/x-www-form-urlencoded',
        )
        self.assertEqual(r.status_code, 201, r.content[:300])
        self.assertEqual(BackupStatus.objects.get(device=self.broken).last_error, 'Timeout::Error: it said "no"')
        r = self.client.post(HOOK, {'event': 'node_success', 'node': 'quiet-switch'}, content_type='application/json')
        self.assertEqual(r.status_code, 201, r.content[:300])

    def test_hook_rejects_bad_input_and_unknown_node(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(HOOK, {'event': 'nodes_done', 'node': 'quiet-switch'}).status_code, 400)
        self.assertEqual(self.client.post(HOOK, {'event': 'node_success'}).status_code, 400)
        self.assertEqual(self.client.post(HOOK, {'event': 'node_success', 'node': 'nope'}).status_code, 404)

    def test_hook_needs_its_permission_and_device_view(self):
        viewer = get_user_model().objects.create_user('hook-viewer', password='x')
        perm = ObjectPermission.objects.create(name='view devices (hook)', actions=['view'])
        perm.object_types.add(ObjectType.objects.get_for_model(Device))
        perm.users.add(viewer)
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(HOOK, {'event': 'node_success', 'node': 'quiet-switch'}).status_code, 403)

        perm2 = ObjectPermission.objects.create(name='report backup status', actions=['add'])
        perm2.object_types.add(ObjectType.objects.get_for_model(BackupStatus))
        perm2.users.add(viewer)
        viewer = get_user_model().objects.get(pk=viewer.pk)  # drop the cached permissions
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(HOOK, {'event': 'node_success', 'node': 'quiet-switch'}).status_code, 201)

    # ---- nodes.json pull ----
    @mock.patch('netbox_oxidized_viewer.health.fetch_nodes')
    def test_pull_statuses_from_nodes_json(self, mock_fetch):
        mock_fetch.return_value = [
            {'name': 'quiet-switch', 'last': {'end': '2026-09-18 13:13:13 UTC', 'status': 'success'}},
            {'name': 'broken-switch', 'last': {'end': '2026-09-18 13:14:00 UTC', 'status': 'no_connection'}},
            {'name': 'gone-switch', 'last': None},
            {'name': 'not-in-netbox', 'last': {'end': '2026-09-18 13:15:00 UTC', 'status': 'success'}},
        ]
        self.source.api_url = 'http://oxidized.example:8888'
        self.assertEqual(health.pull_statuses(self.source), 2)
        quiet = BackupStatus.objects.get(device=self.quiet)
        self.assertEqual(quiet.last_status, 'success')
        self.assertEqual(quiet.last_run, datetime.datetime(2026, 9, 18, 13, 13, 13, tzinfo=datetime.UTC))
        self.assertEqual(BackupStatus.objects.get(device=self.broken).last_status, 'no_connection')
        self.assertFalse(BackupStatus.objects.filter(device=self.gone).exists())
