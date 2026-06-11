import unittest
from unittest.mock import patch, MagicMock

import django
from django.conf import settings
if not settings.configured:
    settings.configure(CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}})
    django.setup()

from netbox_oxidized_viewer.services.cache import CachedGitBackend


class TestCachedGitBackend(unittest.TestCase):
    @patch('netbox_oxidized_viewer.services.cache.cache')
    def test_caching(self, mock_cache):
        mock_backend = MagicMock()
        mock_backend.repo_path = "/path"
        mock_backend.get_latest_commit.return_value = "commit1"

        mock_cache.get.return_value = None

        cached_backend = CachedGitBackend(mock_backend)
        result1 = cached_backend.get_latest_commit("file1")

        self.assertEqual(result1, "commit1")
        mock_backend.get_latest_commit.assert_called_once_with("file1")
        mock_cache.set.assert_called_once()

        # Cache hit
        mock_cache.get.return_value = "commit1"
        mock_backend.reset_mock()
        mock_cache.reset_mock()

        result2 = cached_backend.get_latest_commit("file1")
        self.assertEqual(result2, "commit1")
        mock_backend.get_latest_commit.assert_not_called()
