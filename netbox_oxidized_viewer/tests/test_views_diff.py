"""
Unit tests for the _hunks_to_side_by_side helper in views.py — the logic that
turns unified-diff hunks into aligned side-by-side rows for the diff template.
"""

from types import SimpleNamespace

from django.test import SimpleTestCase

from netbox_oxidized_viewer.views import _hunks_to_side_by_side


def _hunk(lines):
    """A minimal stand-in for DiffHunk — the helper only reads .lines."""
    return SimpleNamespace(lines=lines)


class TestHunksToSideBySide(SimpleTestCase):

    def test_empty(self):
        self.assertEqual(_hunks_to_side_by_side([]), [])

    def test_context_only(self):
        rows = _hunks_to_side_by_side([_hunk([(' ', 'a'), (' ', 'b')])])[0]['rows']
        self.assertEqual(
            rows,
            [
                {'type': 'context', 'old': 'a', 'new': 'a'},
                {'type': 'context', 'old': 'b', 'new': 'b'},
            ],
        )

    def test_paired_change(self):
        # one deletion + one addition flushed by a following context line
        rows = _hunks_to_side_by_side(
            [_hunk([('-', 'old'), ('+', 'new'), (' ', 'ctx')])]
        )[0]['rows']
        self.assertEqual(
            rows,
            [
                {'type': 'change', 'old': 'old', 'new': 'new'},
                {'type': 'context', 'old': 'ctx', 'new': 'ctx'},
            ],
        )

    def test_uneven_addition(self):
        # one deletion, two additions → second addition has no old counterpart
        rows = _hunks_to_side_by_side(
            [_hunk([('-', 'd'), ('+', 'a1'), ('+', 'a2')])]
        )[0]['rows']
        self.assertEqual(
            rows,
            [
                {'type': 'change', 'old': 'd', 'new': 'a1'},
                {'type': 'change', 'old': None, 'new': 'a2'},
            ],
        )

    def test_pending_flushed_at_end_of_hunk(self):
        # trailing change with no closing context line must still be emitted
        rows = _hunks_to_side_by_side([_hunk([('-', 'gone')])])[0]['rows']
        self.assertEqual(rows, [{'type': 'change', 'old': 'gone', 'new': None}])

    def test_meta_preserved(self):
        h = _hunk([(' ', 'x')])
        result = _hunks_to_side_by_side([h])
        self.assertIs(result[0]['meta'], h)
