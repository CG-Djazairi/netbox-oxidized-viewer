"""Request-path regressions using real Git objects and NetBox authentication."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from users.models import Token

from netbox_oxidized_viewer.models import BackupStatus, ConfigCommitNote, ConfigSnapshot, OxidizedSource

from .test_api import _grant, _token_auth
from .test_tasks import _build_repo


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class TestReleaseRequests(TestCase):
    @classmethod
    def setUpTestData(cls):
        site = Site.objects.create(name='review', slug='review')
        maker = Manufacturer.objects.create(name='review', slug='review')
        kind = DeviceType.objects.create(manufacturer=maker, model='review', slug='review')
        role = DeviceRole.objects.create(name='review', slug='review')
        cls.device = Device.objects.create(name='sw', site=site, device_type=kind, role=role)
        cls.admin = get_user_model().objects.create_superuser('review')

    def setUp(self):
        cache.clear()
        self.repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.repo)
        self.old = _build_repo(self.repo, {'other': 'unchanged\n'})
        self.new = _build_repo(self.repo, {'other': 'unchanged\n', 'sw': '-- old\n'}, parent=self.old)
        self.source = OxidizedSource.objects.create(name='review', git_repo_path=self.repo, api_url='http://oxi:8888')
        self.api = f'/api/plugins/oxidized-viewer/devices/{self.device.pk}'
        self.ui = f'/plugins/oxidized-viewer/devices/{self.device.pk}'
        self.client.force_login(self.admin)

    def url(self, name, **kwargs):
        return reverse(f'plugins:netbox_oxidized_viewer:{name}', kwargs={'pk': self.device.pk, **kwargs})

    def test_query_sha_normalization(self):
        for sha in (self.new, self.new.upper()):
            response = self.client.get(f'{self.api}/config/', {'sha': sha})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['commit_sha'], self.new)

    def test_query_sha_validation(self):
        self.client.raise_request_exception = False
        for sha in ('a' * 7, 'z' * 40, 'é' * 40, 'a' * 41):
            with self.subTest(sha=sha):
                self.assertEqual(self.client.get(f'{self.api}/config/', {'sha': sha}).status_code, 400)
            with self.subTest(compare_sha=sha):
                self.assertEqual(self.client.get(self.url('device_compare'), {'sha_new': sha}).status_code, 400)

    def test_sha_paths_reject_bad_values_and_normalize_uppercase(self):
        url = self.url('device_commit_download', sha=self.new)
        response = self.client.get(url.replace(self.new, self.new.upper()))
        self.assertEqual(response.content, b'-- old\n')
        for sha in ('a' * 7, 'z' * 40, 'a' * 41):
            self.assertEqual(self.client.get(url.replace(self.new, sha)).status_code, 404)

    def test_ui_note_length_limit(self):
        url = self.url('device_add_note', sha=self.new)
        for message in (' ', 'x' * 2001):
            self.assertEqual(self.client.post(url, {'message': message}).status_code, 302)
            self.assertFalse(ConfigCommitNote.objects.exists())
        self.client.post(url, {'message': '  ' + 'x' * 2000 + '  '})
        self.assertEqual(ConfigCommitNote.objects.get().message, 'x' * 2000)

    def test_empty_creation_visible_in_api_and_template(self):
        empty = _build_repo(self.repo, {'other': 'unchanged\n', 'sw': ''}, parent=self.new)
        url = f'{self.api}/diff/{self.old}/{empty}/'
        for _ in range(2):  # Exercise the cached FileDiff too.
            result = self.client.get(url).json()
            self.assertEqual((result['old_exists'], result['new_exists']), (False, True))
            self.assertEqual(result['hunks'], [])
        response = self.client.get(self.url('device_diff', sha_old=self.old, sha_new=empty))
        self.assertContains(response, 'File created')
        self.assertNotContains(response, 'No changes found')
        response = self.client.get(self.url('device_diff', sha_old=empty, sha_new=self.old))
        self.assertContains(response, 'File deleted')

    @unittest.skipUnless(shutil.which('git'), 'git executable not available (netbox-docker images ship without it)')
    def test_downloaded_text_patches_apply(self):
        changed = _build_repo(self.repo, {'sw': '++ new\n'}, parent=self.new)
        cases = (
            (self.old, self.new, None, b'-- old\n'),
            (self.new, changed, b'-- old\n', b'++ new\n'),
            (self.new, self.old, b'-- old\n', None),
            (self.new, self.new, b'-- old\n', b'-- old\n'),
        )
        for old, new, before, after in cases:
            with self.subTest(old=old, new=new), tempfile.TemporaryDirectory() as target:
                response = self.client.get(self.url('device_diff_download', sha_old=old, sha_new=new))
                self.assertEqual(response.status_code, 200)
                path = Path(target) / 'sw'
                if before is not None:
                    path.write_bytes(before)
                patch = response.content
                if before is None:
                    self.assertIn(b'--- /dev/null\n', patch)
                if after is None:
                    self.assertIn(b'+++ /dev/null\n', patch)
                # An unchanged file downloads an empty patch, accepted by Git
                # explicitly as a no-op. Changed text must apply normally.
                args = ['git', 'apply', '--allow-empty', '-'] if old == new else ['git', 'apply', '-']
                applied = subprocess.run(args, input=patch, cwd=target, capture_output=True)
                self.assertEqual(applied.returncode, 0, applied.stderr)
                self.assertEqual(path.read_bytes() if path.exists() else None, after)

    @unittest.skipUnless(shutil.which('git'), 'git executable not available (netbox-docker images ship without it)')
    def test_downloaded_empty_file_changes_apply(self):
        empty = _build_repo(self.repo, {'other': 'unchanged\n', 'sw': ''}, parent=self.new)
        for old, new, existed in ((self.old, empty, False), (empty, self.old, True)):
            with self.subTest(existed=existed), tempfile.TemporaryDirectory() as target:
                path = Path(target) / 'sw'
                if existed:
                    path.write_bytes(b'')
                response = self.client.get(self.url('device_diff_download', sha_old=old, sha_new=new))
                applied = subprocess.run(['git', 'apply', '-'], input=response.content, cwd=target, capture_output=True)
                self.assertEqual(applied.returncode, 0, applied.stderr)
                self.assertEqual(path.exists(), not existed)

    def endpoints(self):
        return (
            ('/api/plugins/oxidized-viewer/hook/', {'event': 'node_success', 'node': 'sw'}, 201),
            (f'{self.api}/commits/{self.new}/note/', {'message': 'change'}, 201),
            (f'{self.api}/sync/', {}, 200),
        )

    @mock.patch('netbox_oxidized_viewer.services.oxidized_api.trigger_backup')
    @mock.patch('netbox_oxidized_viewer.jobs.ConfigSnapshotIndexJob.enqueue')
    def test_anonymous_and_unprivileged_write_tokens_have_no_effect(self, enqueue, trigger):
        self.client.logout()
        user = get_user_model().objects.create_user('no-permissions')
        token = _token_auth(user, True)
        for auth in (None, token):
            for url, data, _ in self.endpoints():
                response = self.client.post(url, data, **({'HTTP_AUTHORIZATION': auth} if auth else {}))
                self.assertIn(response.status_code, (401, 403))
        self.assertFalse(BackupStatus.objects.exists())
        self.assertFalse(ConfigCommitNote.objects.exists())
        enqueue.assert_not_called()
        trigger.assert_not_called()

    @mock.patch('netbox_oxidized_viewer.services.oxidized_api.trigger_backup')
    def test_session_csrf_for_all_write_endpoints(self, trigger):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        endpoints = list(self.endpoints()) + [
            (self.url('device_add_note', sha=self.new), {'message': 'change'}, 302),
            (self.url('device_sync'), {}, 302),
        ]
        for url, data, _ in endpoints:
            self.assertEqual(client.post(url, data).status_code, 403)
        self.assertFalse(BackupStatus.objects.exists())
        self.assertFalse(ConfigCommitNote.objects.exists())
        trigger.assert_not_called()
        client.get(self.url('device_commit', sha_new=self.new))
        csrf = client.cookies['csrftoken'].value
        for url, data, expected in endpoints:
            self.assertEqual(client.post(url, data, HTTP_X_CSRFTOKEN=csrf).status_code, expected)
        self.assertEqual(trigger.call_count, 2)

    @mock.patch('netbox_oxidized_viewer.services.oxidized_api.trigger_backup')
    def test_v2_tokens_enforce_write_flag(self, trigger):
        if not hasattr(Token, 'version'):
            self.skipTest('NetBox 4.3 does not support v2 tokens')
        self.client.logout()
        for enabled in (False, True):
            token = Token.objects.create(user=self.admin, version=2, write_enabled=enabled)
            auth = token.get_auth_header_prefix() + token.token
            for url, data, expected in self.endpoints():
                response = self.client.post(url, data, HTTP_AUTHORIZATION=auth)
                self.assertEqual(response.status_code, expected if enabled else 403, response.content)
            if not enabled:
                self.assertFalse(BackupStatus.objects.exists())
                self.assertFalse(ConfigCommitNote.objects.exists())
                trigger.assert_not_called()

    @mock.patch('netbox_oxidized_viewer.services.oxidized_api.trigger_backup')
    def test_device_constraints_on_writes_and_reads(self, trigger):
        self.client.logout()
        user = get_user_model().objects.create_user('outside-device')
        _grant(user, Device, ['view', 'change'], constraints={'name': 'another'})
        _grant(user, ConfigSnapshot, ['view'])
        _grant(user, BackupStatus, ['add'])
        _grant(user, ConfigCommitNote, ['add'])
        auth = _token_auth(user, True)
        for url, data, _ in self.endpoints():
            self.assertEqual(self.client.post(url, data, HTTP_AUTHORIZATION=auth).status_code, 404)
        for suffix in ('config/', 'history/', f'diff/{self.old}/{self.new}/', f'commits/{self.new}/note/'):
            self.assertEqual(self.client.get(f'{self.api}/{suffix}', HTTP_AUTHORIZATION=auth).status_code, 404)
        self.assertFalse(BackupStatus.objects.exists())
        self.assertFalse(ConfigCommitNote.objects.exists())
        trigger.assert_not_called()

    @mock.patch('netbox_oxidized_viewer.services.oxidized_api.trigger_backup')
    def test_independent_user_permissions_with_write_enabled_tokens(self, trigger):
        self.client.logout()
        grants = (
            (Device, ['view', 'change']),
            (ConfigSnapshot, ['view']),
            (BackupStatus, ['add']),
            (ConfigCommitNote, ['add']),
        )
        for omitted, expected in (
            (Device, (404, 404, 404)),
            (ConfigSnapshot, (201, 403, 403)),
            (BackupStatus, (403, 201, 200)),
            (ConfigCommitNote, (201, 403, 200)),
        ):
            with self.subTest(omitted=omitted.__name__):
                user = get_user_model().objects.create_user('without-' + omitted.__name__)
                for model, actions in grants:
                    if model is not omitted:
                        _grant(user, model, actions)
                auth = _token_auth(user, True)
                for (url, data, _), status in zip(self.endpoints(), expected, strict=True):
                    notes_before = ConfigCommitNote.objects.count()
                    statuses_before = list(BackupStatus.objects.values())
                    calls_before = trigger.call_count
                    response = self.client.post(url, data, HTTP_AUTHORIZATION=auth)
                    self.assertEqual(response.status_code, status)
                    if status >= 400:
                        self.assertEqual(ConfigCommitNote.objects.count(), notes_before)
                        self.assertEqual(list(BackupStatus.objects.values()), statuses_before)
                        self.assertEqual(trigger.call_count, calls_before)
                config_status = 404 if omitted is Device else 403 if omitted is ConfigSnapshot else 200
                self.assertEqual(
                    self.client.get(f'{self.api}/config/', HTTP_AUTHORIZATION=auth).status_code, config_status
                )

    @mock.patch('netbox_oxidized_viewer.jobs.ConfigSnapshotIndexJob.enqueue')
    def test_source_constraint_and_csrf_on_reindex(self, enqueue):
        user = get_user_model().objects.create_user('other-source')
        _grant(user, OxidizedSource, ['change'], constraints={'name': 'another'})
        url = reverse('plugins:netbox_oxidized_viewer:oxidizedsource_reindex', kwargs={'pk': self.source.pk})
        self.client.force_login(user)
        self.assertEqual(self.client.post(url).status_code, 404)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        self.assertEqual(client.post(url).status_code, 403)
        enqueue.assert_not_called()


class TestConcurrentRunReports(TransactionTestCase):
    def test_older_report_cannot_overwrite_newer_on_separate_connections(self):
        import datetime
        import threading
        from concurrent.futures import ThreadPoolExecutor

        from django.db import connection, connections
        from django.db.models.query import QuerySet
        from django.utils import timezone

        from netbox_oxidized_viewer.health import record_run

        if connection.vendor != 'postgresql':
            self.skipTest('Requires PostgreSQL row locks')
        site = Site.objects.create(name='race', slug='race')
        maker = Manufacturer.objects.create(name='race', slug='race')
        kind = DeviceType.objects.create(manufacturer=maker, model='race', slug='race')
        role = DeviceRole.objects.create(name='race', slug='race')
        device = Device.objects.create(name='race', site=site, device_type=kind, role=role)
        now = timezone.now()
        record_run(device, 'success', when=now - datetime.timedelta(hours=2))
        older_read = threading.Event()
        newer_done = threading.Event()
        original_first = QuerySet.first
        thread_state = threading.local()
        connection_ids = []

        def first(qs):
            result = original_first(qs)
            if qs.model is BackupStatus and getattr(thread_state, 'older', False):
                older_read.set()
                # Before the fix, the newer connection commits while the older
                # one is paused after its read. With locking it must wait;
                # release the older transaction after this bounded interval.
                newer_done.wait(1)
            return result

        def report(older):
            thread_state.older = older
            try:
                with connections['default'].cursor() as cursor:
                    cursor.execute('SELECT pg_backend_pid()')
                    connection_ids.append(cursor.fetchone()[0])
                if not older:
                    self.assertTrue(older_read.wait(5))
                record_run(
                    device, 'fail' if older else 'success', when=now - datetime.timedelta(hours=1) if older else now
                )
                if not older:
                    newer_done.set()
            finally:
                connections['default'].close()

        with mock.patch.object(QuerySet, 'first', first), ThreadPoolExecutor(max_workers=2) as executor:
            older = executor.submit(report, True)
            newer = executor.submit(report, False)
            older.result(timeout=15)
            newer.result(timeout=15)
        self.assertEqual(len(set(connection_ids)), 2)
        status = BackupStatus.objects.get(device=device)
        self.assertEqual(status.last_run, now)
        self.assertEqual(status.last_status, 'success')
        self.assertEqual(status.last_success, now)
