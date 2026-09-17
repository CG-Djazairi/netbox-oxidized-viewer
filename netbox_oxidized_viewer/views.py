import datetime
import re

from dcim.models import Device
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, F
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.html import escape
from django.utils.http import content_disposition_header
from django.utils.safestring import mark_safe
from django.views import View
from django.views.generic import TemplateView
from netbox.plugins import get_plugin_config
from netbox.views import generic
from utilities.views import ViewTab, register_model_view

from . import filters, forms, models, tables
from .services.git_backend import CommitNotFound, FileNotFoundAtCommit, GitBackendError
from .utils import (
    get_backend_and_filename_for_device,
    get_source,
    resolve_device_field,
    scope_device_queryset,
)


def _config_attachment(content, filename, content_type='text/plain; charset=utf-8'):
    """Build a plain-text download response with a safely-quoted filename.

    Device-controlled names can contain quotes/non-ASCII; content_disposition_header
    (Django 4.2+) handles RFC 5987 encoding so the header can't be broken or injected.
    """
    response = HttpResponse(content, content_type=content_type)
    response['Content-Disposition'] = content_disposition_header(as_attachment=True, filename=filename)
    return response


def _device_backend_or_404(request, pk):
    """Shared preamble for the raw download views: resolve the RBAC-restricted
    device and its git backend, or raise Http404."""
    device = get_object_or_404(Device.objects.restrict(request.user, 'view'), pk=pk)
    backend, filename = get_backend_and_filename_for_device(device)
    if not backend:
        raise Http404
    return backend, filename


def _hunks_to_side_by_side(hunks):
    """Convert unified diff hunks to a side-by-side row structure for the template."""
    result = []
    for hunk in hunks:
        rows = []
        pending_del = []
        pending_add = []

        for marker, content in hunk.lines:
            if marker == '-':
                pending_del.append(content)
            elif marker == '+':
                pending_add.append(content)
            else:
                n = max(len(pending_del), len(pending_add))
                for i in range(n):
                    rows.append(
                        {
                            'type': 'change',
                            'old': pending_del[i] if i < len(pending_del) else None,
                            'new': pending_add[i] if i < len(pending_add) else None,
                        }
                    )
                pending_del.clear()
                pending_add.clear()
                rows.append({'type': 'context', 'old': content, 'new': content})

        n = max(len(pending_del), len(pending_add))
        for i in range(n):
            rows.append(
                {
                    'type': 'change',
                    'old': pending_del[i] if i < len(pending_del) else None,
                    'new': pending_add[i] if i < len(pending_add) else None,
                }
            )

        result.append({'meta': hunk, 'rows': rows})
    return result


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'netbox_oxidized_viewer/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        devices_data = []
        missing = []
        ok_count = stale_count = 0
        source = get_source()
        stale_after_hours = get_plugin_config('netbox_oxidized_viewer', 'stale_after_hours') or 26

        if source:
            stale_before = timezone.now() - datetime.timedelta(hours=stale_after_hours)
            # Served entirely from the ConfigSnapshot index — a per-device git
            # history walk here cost O(devices × history) per cold load.
            # Restricted to viewable devices, otherwise commit metadata leaks
            # for every device in NetBox.
            viewable = Device.objects.restrict(self.request.user, 'view')
            snapshots = (
                models.ConfigSnapshot.objects.filter(source=source, device__in=viewable)
                .select_related('device')
                # NULLS LAST: rows indexed before the metadata backfill would
                # otherwise sort to the top of a "newest first" list.
                .order_by(F('commit_timestamp').desc(nulls_last=True))
            )
            indexed_ids = set()
            for snap in snapshots:
                indexed_ids.add(snap.device_id)
                is_stale = snap.commit_timestamp is None or snap.commit_timestamp < stale_before
                if is_stale:
                    stale_count += 1
                else:
                    ok_count += 1
                devices_data.append(
                    {
                        'device': snap.device,
                        'filename': resolve_device_field(snap.device, source.node_name_source),
                        'commit_sha': snap.commit_sha,
                        'commit_timestamp': snap.commit_timestamp,
                        'commit_subject': snap.commit_subject,
                        'indexed_at': snap.indexed_at,
                        'is_stale': is_stale,
                    }
                )

            # Never-backed-up: active, in-scope, viewable devices whose node name
            # resolves (so they *should* have a backup) but that have no snapshot.
            # Scope filtering keeps out-of-scope gear (passives, servers, …) off
            # this list — the classic silent-failure case, without the noise.
            candidates = scope_device_queryset(
                source,
                viewable.filter(status='active').exclude(pk__in=indexed_ids),
            )
            for device in candidates.iterator():
                filename = resolve_device_field(device, source.node_name_source)
                if filename:
                    missing.append({'device': device, 'filename': filename})

        context.update(
            {
                'devices_data': devices_data,
                'missing': missing,
                'ok_count': ok_count,
                'stale_count': stale_count,
                'missing_count': len(missing),
                'total_count': len(devices_data),
                'stale_after_hours': stale_after_hours,
                'source': source,
            }
        )
        return context


class OxidizedSourceListView(generic.ObjectListView):
    queryset = models.OxidizedSource.objects.all()
    table = tables.OxidizedSourceTable
    filterset = filters.OxidizedSourceFilterSet
    filterset_form = forms.OxidizedSourceFilterForm


class OxidizedSourceView(generic.ObjectView):
    queryset = models.OxidizedSource.objects.all()


class OxidizedSourceEditView(generic.ObjectEditView):
    queryset = models.OxidizedSource.objects.all()
    form = forms.OxidizedSourceForm

    def get_return_url(self, request, obj=None):
        # Always land on the source's own page after a save, even when the edit
        # was opened from the list (whose return_url would send us back there).
        if obj is not None and obj.pk:
            return obj.get_absolute_url()
        return super().get_return_url(request, obj)

    def get(self, request, *args, **kwargs):
        # Single-source: send the "add" route to editing the existing source
        # rather than showing a create form that clean() would only reject.
        if 'pk' not in kwargs:
            existing = get_source()
            if existing:
                return redirect('plugins:netbox_oxidized_viewer:oxidizedsource_edit', pk=existing.pk)
        return super().get(request, *args, **kwargs)


class OxidizedSourceDeleteView(generic.ObjectDeleteView):
    queryset = models.OxidizedSource.objects.all()


class SourceReindexView(LoginRequiredMixin, View):
    """Enqueue an immediate reindex via NetBox's Jobs framework, so progress and
    logs show up in the core Jobs UI. POST-only (it mutates the index)."""

    def post(self, request, pk):
        source = get_object_or_404(models.OxidizedSource.objects.all(), pk=pk)
        if not request.user.has_perm('netbox_oxidized_viewer.change_oxidizedsource'):
            raise PermissionDenied
        from .jobs import ConfigSnapshotIndexJob

        job = ConfigSnapshotIndexJob.enqueue(instance=source, user=request.user)
        messages.success(
            request,
            f"Reindex enqueued (job #{job.pk}). Progress appears under the source's Jobs.",
        )
        return redirect('plugins:netbox_oxidized_viewer:oxidizedsource', pk=source.pk)


@register_model_view(Device, name='oxidized_config', path='config')
class DeviceConfigView(generic.ObjectView):
    queryset = Device.objects.all()
    template_name = 'netbox_oxidized_viewer/device_config_tab.html'
    tab = ViewTab(label='Config History', permission='dcim.view_device', weight=500)

    def get_extra_context(self, request, instance):
        backend, filename = get_backend_and_filename_for_device(instance)

        config_content = None
        latest_commit = None
        commits = []
        error_message = None

        if backend:
            commits = backend.list_commits(filename, limit=50)
            latest_commit = commits[0] if commits else None
            if latest_commit:
                try:
                    config_content = backend.get_file_content(filename, latest_commit.sha)
                except (CommitNotFound, FileNotFoundAtCommit) as e:
                    error_message = str(e)
            else:
                error_message = f'No configuration backups found for this device (filename: {filename}).'
        else:
            error_message = 'No valid Oxidized Source mapping found for this device or Git repository is invalid.'

        # Per-commit note counts, so the history table can show a badge without
        # a query per row.
        note_counts = {}
        if commits:
            for row in (
                models.ConfigCommitNote.objects.filter(device=instance, commit_sha__in=[c.sha for c in commits])
                .values('commit_sha')
                .annotate(n=Count('pk'))
            ):
                note_counts[row['commit_sha']] = row['n']

        source = get_source()
        return {
            'active_tab': 'oxidized_config',
            'config_content': config_content,
            'latest_commit': latest_commit,
            'commits': commits,
            'error_message': error_message,
            'note_counts': note_counts,
            'sync_enabled': bool(source and source.api_url),
        }


class ConfigDiffView(generic.ObjectView):
    queryset = Device.objects.all()
    template_name = 'netbox_oxidized_viewer/diff.html'

    def get_object(self, **kwargs):
        return super().get_object(pk=self.kwargs.get('pk'))

    def get_extra_context(self, request, instance):
        sha_new = self.kwargs.get('sha_new')
        sha_old = self.kwargs.get('sha_old')

        backend, filename = get_backend_and_filename_for_device(instance)
        diff_data = None
        side_by_side = None
        commits = []
        error = None

        if backend and filename:
            commits = backend.list_commits(filename, limit=100)
            try:
                if not sha_old:
                    for i, c in enumerate(commits):
                        if c.sha == sha_new:
                            if i + 1 < len(commits):
                                sha_old = commits[i + 1].sha
                            break
                if sha_old:
                    diff_data = backend.get_diff(filename, sha_old, sha_new)
            except GitBackendError as e:
                # Bad SHA in the URL, file missing at a commit, etc. — user
                # input problems.  Anything else is a bug and should 500.
                error = str(e)

        if diff_data:
            side_by_side = _hunks_to_side_by_side(diff_data.hunks)

        # Notes annotate the "new" commit — the change being reviewed.
        notes = []
        if sha_new:
            notes = list(
                models.ConfigCommitNote.objects.filter(device=instance, commit_sha=sha_new).select_related('created_by')
            )

        return {
            'diff_data': diff_data,
            'side_by_side': side_by_side,
            'sha_old': sha_old,
            'sha_new': sha_new,
            'commits': commits,
            'error': error,
            'notes': notes,
            'can_add_note': request.user.has_perm('netbox_oxidized_viewer.add_configcommitnote'),
            'active_tab': 'oxidized_config',
        }


# The download views are plain Django Views (no NetBox generic-view mixins), so
# object-level RBAC does not come for free: each lookup must go through
# .restrict() or a user could fetch any device's config by guessing PKs.


class DeviceConfigDownloadView(View):
    def get(self, request, pk):
        backend, filename = _device_backend_or_404(request, pk)
        latest = backend.get_latest_commit(filename)
        if not latest:
            raise Http404
        try:
            content = backend.get_file_content(filename, latest.sha)
        except GitBackendError as exc:
            raise Http404 from exc
        return _config_attachment(content, f'{filename}.txt')


class CommitConfigDownloadView(View):
    def get(self, request, pk, sha):
        backend, filename = _device_backend_or_404(request, pk)
        try:
            content = backend.get_file_content(filename, sha)
        except GitBackendError as exc:
            raise Http404 from exc
        return _config_attachment(content, f'{filename}-{sha[:7]}.txt')


class DiffDownloadView(View):
    def get(self, request, pk, sha_old, sha_new):
        backend, filename = _device_backend_or_404(request, pk)
        try:
            diff_data = backend.get_diff(filename, sha_old, sha_new)
        except GitBackendError as exc:
            raise Http404 from exc

        lines = [f'--- a/{filename}', f'+++ b/{filename}']
        for hunk in diff_data.hunks:
            lines.append(f'@@ -{hunk.old_start},{hunk.old_lines} +{hunk.new_start},{hunk.new_lines} @@')
            for marker, content in hunk.lines:
                lines.append(f'{marker}{content}')
        patch_text = '\n'.join(lines) + '\n'

        return _config_attachment(
            patch_text,
            f'{filename}-{sha_old[:7]}-{sha_new[:7]}.patch',
            content_type='text/x-patch; charset=utf-8',
        )


class ConfigSearchView(LoginRequiredMixin, TemplateView):
    template_name = 'netbox_oxidized_viewer/search.html'

    # Rows rendered per page.
    SEARCH_PAGE_SIZE = 25
    # Matching lines shown per device on the results page.
    MAX_LINES_PER_RESULT = 8

    # Characters a search term may carry into the raw tsquery. Everything else
    # (quotes, backslashes, tsquery operators & | ! ( ) < >, whitespace) is
    # dropped, so user input can never change the shape of the query.
    _TERM_DISALLOWED = re.compile(r'[^\w./:@+-]')

    @classmethod
    def parse_terms(cls, query):
        """Split the query into cleaned, lower-cased search terms."""
        terms = []
        for raw in query.split():
            term = cls._TERM_DISALLOWED.sub('', raw).lower()
            if term:
                terms.append(term)
        return terms

    @staticmethod
    def build_tsquery(terms):
        """
        Prefix-match every term: 'krd':* matches the token krd and any token that
        starts with it; '10.10.10':* matches 10.10.10.9 and 10.10.10.10 (an IP is a
        single token for Postgres, so an exact-token search on part of it finds
        nothing). Quoting keeps dots, slashes and hyphens inside one lexeme.
        """
        raw = ' & '.join(f"'{term}':*" for term in terms)
        return SearchQuery(raw, search_type='raw', config='simple')

    @staticmethod
    def render_line(line, terms):
        """Escape a config line, then mark the search terms in the escaped text.
        Config content is device-controlled (banners, descriptions) and must never
        reach the page as live HTML."""
        escaped = escape(line)
        pattern = re.compile(
            '|'.join(re.escape(escape(term)) for term in sorted(terms, key=len, reverse=True)),
            re.IGNORECASE,
        )
        return mark_safe(pattern.sub(lambda m: f'<mark>{m.group(0)}</mark>', escaped))

    @classmethod
    def matching_lines(cls, content, terms):
        """Return (lines, total): numbered lines containing any term (capped) and
        the total count of such lines."""
        lines = []
        total = 0
        for lineno, line in enumerate(content.splitlines(), start=1):
            lowered = line.lower()
            if any(term in lowered for term in terms):
                total += 1
                if len(lines) < cls.MAX_LINES_PER_RESULT:
                    lines.append({'lineno': lineno, 'html': cls.render_line(line, terms)})
        return lines, total

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get('q', '').strip()
        results = []
        error = None
        total_count = 0
        page_obj = None

        terms = self.parse_terms(query) if len(query) >= 2 else []
        if terms:
            source = get_source()
            if source:
                results, total_count, page_obj = self._fts_search(terms, source)
            else:
                error = 'No Oxidized source configured.'

        context.update(
            {
                'query': query,
                'results': results,
                'page_obj': page_obj,
                'error': error,
                'total_count': total_count,
                'page_size': self.SEARCH_PAGE_SIZE,
            }
        )
        return context

    def _fts_search(self, terms, source):
        sq = self.build_tsquery(terms)
        # Restrict to devices the requesting user has view permission for.
        allowed_devices = Device.objects.restrict(self.request.user, 'view')

        # Rank-ordered queryset of bare PKs, so COUNT(*) and the OFFSET/LIMIT page
        # slice stay cheap regardless of total match count.
        ranked_pks = (
            models.ConfigSnapshot.objects.filter(source=source, search_vector=sq, device__in=allowed_devices)
            # F('search_vector') references the stored tsvector column directly;
            # passing the bare name re-runs to_tsvector() on it and zeroes the rank.
            .annotate(rank=SearchRank(F('search_vector'), sq))
            .order_by('-rank', 'device_id')
            .values_list('pk', flat=True)
        )

        paginator = Paginator(ranked_pks, self.SEARCH_PAGE_SIZE)
        page_obj = paginator.get_page(self.request.GET.get('page') or 1)

        # Re-fetch only this page's rows with their content, preserving rank order.
        page_qs = (
            models.ConfigSnapshot.objects.filter(pk__in=list(page_obj.object_list))
            .annotate(rank=SearchRank(F('search_vector'), sq))
            .select_related('device')
            .order_by('-rank', 'device_id')
        )

        results = []
        for snap in page_qs:
            lines, match_count = self.matching_lines(snap.content, terms)
            results.append(
                {
                    'device': snap.device,
                    'lines': lines,
                    'match_count': match_count,
                    'more_lines': max(match_count - len(lines), 0),
                    'commit_sha': snap.commit_sha,
                    'indexed_at': snap.indexed_at,
                }
            )
        return results, paginator.count, page_obj


class ConfigCompareRedirectView(View):
    def get(self, request, pk):
        sha_old = request.GET.get('sha_old', '').strip()
        sha_new = request.GET.get('sha_new', '').strip()
        if sha_old and sha_new and sha_old != sha_new:
            return redirect('plugins:netbox_oxidized_viewer:device_diff', pk=pk, sha_old=sha_old, sha_new=sha_new)
        elif sha_new:
            return redirect('plugins:netbox_oxidized_viewer:device_commit', pk=pk, sha_new=sha_new)
        return redirect('plugins:netbox_oxidized_viewer:device_oxidized_config', pk=pk)


class AddCommitNoteView(LoginRequiredMixin, View):
    """Attach a note to a commit. Stored in NetBox (see ConfigCommitNote) — the
    git repo is never written to. POST-only."""

    def post(self, request, pk, sha):
        device = get_object_or_404(Device.objects.restrict(request.user, 'view'), pk=pk)
        if not request.user.has_perm('netbox_oxidized_viewer.add_configcommitnote'):
            raise PermissionDenied
        message = request.POST.get('message', '').strip()
        if message:
            models.ConfigCommitNote.objects.create(
                device=device,
                commit_sha=sha,
                message=message,
                created_by=request.user,
            )
            messages.success(request, 'Note added.')
        else:
            messages.warning(request, 'Note was empty — nothing saved.')
        return redirect('plugins:netbox_oxidized_viewer:device_commit', pk=device.pk, sha_new=sha)


class DeviceSyncView(LoginRequiredMixin, View):
    """Trigger an on-demand Oxidized backup for a device (optional; requires the
    source's api_url). Read-only toward git — it just asks Oxidized to poll."""

    def post(self, request, pk):
        device = get_object_or_404(Device.objects.restrict(request.user, 'view'), pk=pk)
        source = get_source()
        if not source or not source.api_url:
            messages.error(request, 'No Oxidized API URL is configured on the source.')
            return redirect('plugins:netbox_oxidized_viewer:device_oxidized_config', pk=device.pk)

        node = resolve_device_field(device, source.node_name_source)
        if not node:
            messages.error(request, "Could not resolve this device's Oxidized node name.")
            return redirect('plugins:netbox_oxidized_viewer:device_oxidized_config', pk=device.pk)

        from .services.oxidized_api import OxidizedAPIError, trigger_backup

        try:
            trigger_backup(source.api_url, node)
            messages.success(
                request,
                f"Backup requested for '{node}'. Oxidized will poll it shortly; "
                'reindex or wait for the next cycle to see the new commit.',
            )
        except OxidizedAPIError as exc:
            messages.error(request, str(exc))
        return redirect('plugins:netbox_oxidized_viewer:device_oxidized_config', pk=device.pk)
