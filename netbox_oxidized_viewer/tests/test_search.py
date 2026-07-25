"""
Tests for the ConfigSnapshot model, FTS search_vector signal, and the
ConfigSearchView FTS rewrite.

Three classes:
  TestConfigSnapshotSignal   — unit: signal keeps search_vector in sync
  TestFTSSearchIntegration   — integration: 50 synthetic devices, ranked results
  TestLabIntegration         — lab: real git repo, skipped if repo absent
"""

import os
import unittest

from django.contrib.auth import get_user_model
from django.contrib.postgres.search import SearchQuery
from django.test import TestCase, RequestFactory

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site

from netbox_oxidized_viewer.models import ConfigSnapshot, OxidizedSource
from netbox_oxidized_viewer.views import ConfigSearchView


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

def _make_device_fixtures():
    """Return (site, manufacturer, device_type, device_role) for test setup."""
    site = Site.objects.create(name='Test Site', slug='test-site')
    manufacturer = Manufacturer.objects.create(name='Nokia', slug='nokia')
    device_type = DeviceType.objects.create(
        manufacturer=manufacturer, model='SR Linux', slug='sr-linux'
    )
    device_role = DeviceRole.objects.create(name='Router', slug='router')
    return site, manufacturer, device_type, device_role


SR_LINUX_INTERFACE = """\
set / interface ethernet-1/1 admin-state enable
set / interface ethernet-1/1 subinterface 0 ipv4 admin-state enable
set / interface ethernet-1/1 subinterface 0 ipv4 address 10.0.0.1/31
"""

SR_LINUX_BGP = """\
set / network-instance default protocols bgp autonomous-system 65001
set / network-instance default protocols bgp router-id 10.0.0.1
set / network-instance default protocols bgp group EBGP export-policy ACCEPT
"""


# ---------------------------------------------------------------------------
# 1. Unit: search_vector signal
# ---------------------------------------------------------------------------

class TestConfigSnapshotSignal(TestCase):

    @classmethod
    def setUpTestData(cls):
        site, manufacturer, device_type, device_role = _make_device_fixtures()
        cls.device = Device.objects.create(
            name='spine1', site=site, device_type=device_type, role=device_role
        )
        cls.source = OxidizedSource.objects.create(
            name='Test Source', git_repo_path='/tmp/fake-repo'
        )

    def _make_snap(self, content, sha='abc1234567890abc1234567890abc1234567890a'):
        return ConfigSnapshot.objects.create(
            device=self.device,
            source=self.source,
            content=content,
            commit_sha=sha,
        )

    def test_search_vector_populated_after_create(self):
        snap = self._make_snap(SR_LINUX_INTERFACE)
        snap.refresh_from_db()
        self.assertIsNotNone(snap.search_vector)

    def test_search_vector_matches_term_in_content(self):
        self._make_snap(SR_LINUX_INTERFACE)
        sq = SearchQuery('interface', config='simple')
        self.assertTrue(
            ConfigSnapshot.objects.filter(
                device=self.device, search_vector=sq
            ).exists()
        )

    def test_search_vector_does_not_match_absent_term(self):
        self._make_snap(SR_LINUX_INTERFACE)
        sq = SearchQuery('bgp', config='simple')
        self.assertFalse(
            ConfigSnapshot.objects.filter(
                device=self.device, search_vector=sq
            ).exists()
        )

    def test_search_vector_updated_on_content_change(self):
        snap = self._make_snap(SR_LINUX_INTERFACE)

        snap.content = SR_LINUX_BGP
        snap.save()
        snap.refresh_from_db()

        interface_sq = SearchQuery('interface', config='simple')
        bgp_sq = SearchQuery('bgp', config='simple')

        self.assertFalse(
            ConfigSnapshot.objects.filter(pk=snap.pk, search_vector=interface_sq).exists(),
            "'interface' should no longer match after content replaced with BGP config",
        )
        self.assertTrue(
            ConfigSnapshot.objects.filter(pk=snap.pk, search_vector=bgp_sq).exists(),
            "'bgp' should match after content updated",
        )

    def test_queryset_update_refreshes_vector(self):
        """
        search_vector is a STORED generated column, so a queryset .update()
        (which bypasses save() and any signal) still refreshes it. This is the
        bug class the old post_save signal could not cover.
        """
        snap = self._make_snap(SR_LINUX_INTERFACE)
        ConfigSnapshot.objects.filter(pk=snap.pk).update(content=SR_LINUX_BGP)

        interface_sq = SearchQuery('interface', config='simple')
        bgp_sq = SearchQuery('bgp', config='simple')
        self.assertFalse(
            ConfigSnapshot.objects.filter(pk=snap.pk, search_vector=interface_sq).exists(),
            "'interface' must no longer match after queryset .update() to BGP config",
        )
        self.assertTrue(
            ConfigSnapshot.objects.filter(pk=snap.pk, search_vector=bgp_sq).exists(),
            "'bgp' must match after queryset .update() — generated column stays in sync",
        )


# ---------------------------------------------------------------------------
# 2. Integration: 50 synthetic devices, ranked search, headlines
# ---------------------------------------------------------------------------

DEVICE_COUNT = 50

# Every device has this base; MATCH_EVERY is searchable across all.
MATCH_EVERY = "set / system banner login Welcome"
# Only even-numbered devices also contain this.
MATCH_EVEN = "set / interface loopback0 admin-state enable"


class TestFTSSearchIntegration(TestCase):

    @classmethod
    def setUpTestData(cls):
        site, manufacturer, device_type, device_role = _make_device_fixtures()
        cls.source = OxidizedSource.objects.create(
            name='Integration Source', git_repo_path='/tmp/fake-repo-integration'
        )

        devices = Device.objects.bulk_create([
            Device(
                name=f'device-{i:03d}',
                site=site,
                device_type=device_type,
                role=device_role,
            )
            for i in range(DEVICE_COUNT)
        ])

        ConfigSnapshot.objects.bulk_create([
            ConfigSnapshot(
                device=dev,
                source=cls.source,
                content=(
                    MATCH_EVERY + '\n' + (MATCH_EVEN if i % 2 == 0 else SR_LINUX_BGP)
                ),
                commit_sha='a' * 39 + str(i % 10),
            )
            for i, dev in enumerate(devices)
        ])
        # search_vector is a STORED generated column — Postgres populates it on
        # INSERT, so even bulk_create() produces searchable rows with no extra step.

    def _search(self, term):
        sq = SearchQuery(term, config='simple')
        from django.contrib.postgres.search import SearchHeadline, SearchRank
        from django.db.models import F
        return (
            ConfigSnapshot.objects
            .filter(source=self.source, search_vector=sq)
            .annotate(rank=SearchRank(F('search_vector'), sq))
            .annotate(headline=SearchHeadline(
                'content', sq, config='simple',
                start_sel='<mark>', stop_sel='</mark>',
                max_words=30, min_words=10, max_fragments=2,
            ))
            .order_by('-rank')
        )

    def test_all_devices_match_common_term(self):
        qs = self._search('welcome')
        self.assertEqual(qs.count(), DEVICE_COUNT)

    def test_only_even_devices_match_interface(self):
        qs = self._search('interface')
        self.assertEqual(qs.count(), DEVICE_COUNT // 2)

    def test_odd_devices_match_bgp(self):
        qs = self._search('bgp')
        self.assertEqual(qs.count(), DEVICE_COUNT // 2)

    def test_results_are_ranked(self):
        qs = list(self._search('welcome'))
        ranks = [r.rank for r in qs]
        # All ranks should be non-zero and results come back ordered descending.
        self.assertTrue(all(r > 0 for r in ranks))
        self.assertEqual(ranks, sorted(ranks, reverse=True))

    def test_headline_contains_mark_tags(self):
        qs = self._search('welcome')
        first = qs.first()
        self.assertIsNotNone(first)
        self.assertIn('<mark>', first.headline)
        self.assertIn('</mark>', first.headline)

    def test_no_false_positives(self):
        qs = self._search('ospf')
        self.assertEqual(qs.count(), 0)

    def test_case_insensitive(self):
        # 'simple' config lowercases tokens; search with uppercase should match.
        qs_lower = self._search('welcome')
        qs_upper = self._search('WELCOME')
        self.assertEqual(qs_lower.count(), qs_upper.count())


# ---------------------------------------------------------------------------
# 3. View: headline escaping (stored-XSS regression)
# ---------------------------------------------------------------------------

class TestSearchHeadlineEscaping(TestCase):
    """
    Config content is device-controlled (banners, descriptions).  The view must
    escape it before marking the headline safe — a <script> tag in a config
    must never reach the page as live HTML.
    """

    @classmethod
    def setUpTestData(cls):
        site, manufacturer, device_type, device_role = _make_device_fixtures()
        cls.user = get_user_model().objects.create_superuser('search-admin')
        cls.source = OxidizedSource.objects.create(
            name='XSS Source', git_repo_path='/tmp/fake-repo-xss'
        )
        device = Device.objects.create(
            name='evil-banner', site=site, device_type=device_type, role=device_role
        )
        # search_vector is a STORED generated column, populated by Postgres on insert.
        ConfigSnapshot.objects.create(
            device=device,
            source=cls.source,
            content=(
                'set / system banner login '
                '<script>alert(1)</script> interface admin-state enable'
            ),
            commit_sha='b' * 40,
        )

    def _search_context(self, query):
        request = RequestFactory().get('/search/', {'q': query})
        request.user = self.user
        view = ConfigSearchView()
        view.request = request
        return view.get_context_data()

    def test_render_headline_escapes_html(self):
        # The core guarantee: raw headline HTML is escaped, only the sentinel
        # markers become live <mark> tags.
        raw = '<script>alert(1)</script> \x01interface\x02'
        rendered = ConfigSearchView._render_headline(raw)
        self.assertEqual(
            rendered,
            '&lt;script&gt;alert(1)&lt;/script&gt; <mark>interface</mark>',
        )

    def test_no_live_html_reaches_search_results(self):
        # End-to-end: whatever ts_headline returns (it strips well-formed tags
        # itself, but that's parser behaviour we must not rely on), the only
        # '<' in the final headline belongs to our own <mark> tags.
        context = self._search_context('interface')
        self.assertEqual(len(context['results']), 1)
        headline = context['results'][0]['headline']
        self.assertIn('<mark>interface</mark>', headline)
        stripped = headline.replace('<mark>', '').replace('</mark>', '')
        self.assertNotIn('<', stripped)


# ---------------------------------------------------------------------------
# 4. Lab integration: real git repo, skipped if absent
# ---------------------------------------------------------------------------

LAB_REPO_PATH = os.environ.get('OXIDIZED_LAB_REPO', '')

@unittest.skipUnless(
    LAB_REPO_PATH and os.path.exists(LAB_REPO_PATH),
    "Set OXIDIZED_LAB_REPO to a bare repo path to run lab integration",
)
class TestLabIntegration(TestCase):

    @classmethod
    def setUpTestData(cls):
        site, manufacturer, device_type, device_role = _make_device_fixtures()
        cls.source = OxidizedSource.objects.create(
            name='Lab Source',
            git_repo_path=LAB_REPO_PATH,
        )
        cls.devices = {}
        for hostname in ('spine1', 'leaf1', 'leaf2'):
            cls.devices[hostname] = Device.objects.create(
                name=hostname, site=site, device_type=device_type, role=device_role
            )

    def _index(self):
        """Run the indexing task synchronously for the lab source."""
        from netbox_oxidized_viewer.tasks import update_config_snapshots
        update_config_snapshots(source_pk=self.source.pk)

    def test_all_lab_devices_indexed(self):
        self._index()
        indexed = ConfigSnapshot.objects.filter(source=self.source)
        indexed_names = set(indexed.values_list('device__name', flat=True))
        for hostname in ('spine1', 'leaf1', 'leaf2'):
            self.assertIn(
                hostname, indexed_names,
                f"{hostname} was not indexed — check that its config file exists in the lab repo",
            )

    def test_interface_search_hits_all_three(self):
        self._index()
        sq = SearchQuery('interface', config='simple')
        results = ConfigSnapshot.objects.filter(source=self.source, search_vector=sq)
        result_names = set(results.values_list('device__name', flat=True))
        for hostname in ('spine1', 'leaf1', 'leaf2'):
            self.assertIn(hostname, result_names)

    def test_reindex_skips_unchanged_devices(self):
        self._index()
        first_shas = {
            snap.device.name: snap.commit_sha
            for snap in ConfigSnapshot.objects.filter(source=self.source)
        }
        # Second run — nothing in the repo changed, so all should be skipped.
        self._index()
        second_shas = {
            snap.device.name: snap.commit_sha
            for snap in ConfigSnapshot.objects.filter(source=self.source)
        }
        self.assertEqual(first_shas, second_shas)
