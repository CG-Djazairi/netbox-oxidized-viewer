"""
Behavioral tests for CachedGitBackend against a real (locmem) cache and a
call-counting stub backend — no mocking of the cache module, so these assert
what a caller actually observes: memoization, key isolation, and that a
None result (device with no backups) is cached rather than re-walked forever.
"""

from unittest import mock

import requests
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from netbox_oxidized_viewer.services.cache import CachedGitBackend
from netbox_oxidized_viewer.services.oxidized_api import OxidizedAPIError, trigger_backup


class _StubBackend:
    """Records how many times each expensive method actually ran."""

    def __init__(self, repo_path='/repo'):
        self.repo_path = repo_path
        self.calls = {'latest': 0, 'commits': 0, 'content': 0}
        self.latest_return = 'commit-latest'

    def get_latest_commit(self, filename):
        self.calls['latest'] += 1
        return self.latest_return

    def list_commits(self, filename, limit=50):
        self.calls['commits'] += 1
        return [f'{filename}:{limit}']

    def get_file_content(self, filename, sha):
        self.calls['content'] += 1
        return f'content of {filename}@{sha}'


@override_settings(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
class TestCachedGitBackend(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.stub = _StubBackend()
        self.cached = CachedGitBackend(self.stub)

    def test_second_call_is_memoized(self):
        self.assertEqual(self.cached.get_latest_commit('dev1'), 'commit-latest')
        self.assertEqual(self.cached.get_latest_commit('dev1'), 'commit-latest')
        self.assertEqual(self.stub.calls['latest'], 1)  # backend hit once

    def test_none_result_is_cached_not_rewalked(self):
        """A device with no backups must not re-walk the repo on every request."""
        self.stub.latest_return = None
        self.assertIsNone(self.cached.get_latest_commit('never-backed-up'))
        self.assertIsNone(self.cached.get_latest_commit('never-backed-up'))
        self.assertEqual(self.stub.calls['latest'], 1)  # None was cached

    def test_distinct_filenames_do_not_share_cache(self):
        self.cached.get_file_content('devA', 'sha1')
        self.cached.get_file_content('devB', 'sha1')
        self.assertEqual(self.stub.calls['content'], 2)
        # And each is independently memoized.
        self.cached.get_file_content('devA', 'sha1')
        self.assertEqual(self.stub.calls['content'], 2)

    def test_distinct_repos_do_not_share_cache(self):
        other = CachedGitBackend(_StubBackend(repo_path='/other-repo'))
        self.cached.get_latest_commit('dev1')
        other.get_latest_commit('dev1')
        # Different repo_path → different key → both backends were consulted.
        self.assertEqual(self.stub.calls['latest'], 1)
        self.assertEqual(other.backend.calls['latest'], 1)

    def test_list_commits_keyed_by_limit(self):
        self.cached.list_commits('dev1', limit=50)
        self.cached.list_commits('dev1', limit=100)
        self.assertEqual(self.stub.calls['commits'], 2)  # different limit → distinct key


class TestOxidizedTrigger(SimpleTestCase):
    def test_empty_url_raises(self):
        with self.assertRaises(OxidizedAPIError):
            trigger_backup('', 'node1')

    @mock.patch('netbox_oxidized_viewer.services.oxidized_api.requests.get')
    def test_success_returns_body_and_builds_url(self, mock_get):
        resp = mock.Mock()
        resp.text = 'queued'
        resp.raise_for_status = mock.Mock()
        mock_get.return_value = resp
        self.assertEqual(trigger_backup('http://oxi:8888/', 'sw1'), 'queued')
        self.assertEqual(mock_get.call_args[0][0], 'http://oxi:8888/node/next/sw1')
        # Never follow redirects: NetBox must only talk to the configured host.
        self.assertIs(mock_get.call_args[1]['allow_redirects'], False)

    @mock.patch('netbox_oxidized_viewer.services.oxidized_api.requests.get')
    def test_non_http_scheme_rejected(self, mock_get):
        for url in ('ftp://oxi:8888', 'file:///etc/passwd', 'oxi:8888'):
            with self.subTest(url=url):
                with self.assertRaises(OxidizedAPIError):
                    trigger_backup(url, 'sw1')
        mock_get.assert_not_called()

    @mock.patch('netbox_oxidized_viewer.services.oxidized_api.requests.get')
    def test_connection_error_wrapped(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError('boom')
        with self.assertRaises(OxidizedAPIError):
            trigger_backup('http://oxi:8888', 'sw1')
