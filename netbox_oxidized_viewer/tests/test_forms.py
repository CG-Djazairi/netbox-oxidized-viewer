"""
Tests for OxidizedSourceForm.clean_git_repo_path — a mistyped path must be
rejected at save time, not silently accepted and surfaced later as an empty
Config History tab.
"""

import shutil
import tempfile

from django.test import TestCase

from netbox_oxidized_viewer.forms import OxidizedSourceForm

from .test_tasks import _build_repo


class TestOxidizedSourceForm(TestCase):
    def _form(self, path):
        return OxidizedSourceForm(
            data={
                'name': 'Lab',
                'git_repo_path': path,
                'node_name_source': 'name',
            }
        )

    def test_valid_repo_passes(self):
        repo_path = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo_path, ignore_errors=True)
        _build_repo(repo_path, {'spine1': 'hostname spine1\n'})
        form = self._form(repo_path)
        self.assertTrue(form.is_valid(), form.errors)

    def test_missing_path_rejected(self):
        form = self._form('/nonexistent/path/xyz123')
        self.assertFalse(form.is_valid())
        self.assertIn('git_repo_path', form.errors)

    def test_non_git_directory_rejected(self):
        empty = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        form = self._form(empty)
        self.assertFalse(form.is_valid())
        self.assertIn('git_repo_path', form.errors)
