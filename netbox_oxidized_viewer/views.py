import datetime

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.postgres.search import SearchHeadline, SearchQuery, SearchRank
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, F
from django.http import HttpResponse, Http404
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
from dcim.models import Device
from . import models, forms, tables, filters
from .utils import (
    get_backend_and_filename_for_device,
    get_source,
    resolve_device_field,
    scope_device_queryset,
)
from .services.git_backend import CommitNotFound, FileNotFoundAtCommit, GitBackendError


def _config_attachment(content, filename, content_type='text/plain; charset=utf-8'):
    """Build a plain-text download response with a safely-quoted filename.

    Device-controlled names can contain quotes/non-ASCII; content_disposition_header
    (Django 4.2+) handles RFC 5987 encoding so the header can't be broken or injected.
    """
    response = HttpResponse(content, content_type=content_type)
    response['Content-Disposition'] = content_disposition_header(
        as_attachment=True, filename=filename
    )
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
                    rows.append({
                        'type': 'change',
                        'old': pending_del[i] if i < len(pending_del) else None,
                        'new': pending_add[i] if i < len(pending_add) else None,
                    })
                pending_del.clear()
                pending_add.clear()
                rows.append({'type': 'context', 'old': content, 'new': content})

        n = max(len(pending_del), len(pending_add))
        for i in range(n):
            rows.append({
                'type': 'change',
                'old': pending_del[i] if i < len(pending_del) else None,
                'new': pending_add[i] if i < len(pending_add) else None,
            })

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
        stale_after_hours = get_plugin_config(
            'netbox_oxidized_viewer', 'stale_after_hours'
        ) or 26

        if source:
            stale_before = timezone.now() - datetime.timedelta(hours=stale_after_hours)
            # Served entirely from the ConfigSnapshot index — a per-device git
            # history walk here cost O(devices × history) per cold load.
            # Restricted to viewable devices, otherwise commit metadata leaks
            # for every device in NetBox.
            viewable = Device.objects.restrict(self.request.user, 'view')
            snapshots = (
                models.ConfigSnapshot.objects
                .filter(source=source, device__in=viewable)
                .select_related('device')
                # NULLS LAST: rows indexed before the metadata backfill would
                # otherwise sort to the top of a "newest first" list.
                .order_by(F('commit_timestamp').desc(nulls_last=True))
            )
            indexed_ids = set()
            for snap in snapshots:
                indexed_ids.add(snap.device_id)
                is_stale = (
                    snap.commit_timestamp is None
                    or snap.commit_timestamp < stale_before
                )
                if is_stale:
                    stale_count += 1
                else:
                    ok_count += 1
                devices_data.append({
                    'device': snap.device,
                    'filename': resolve_device_field(snap.device, source.node_name_source),
                    'commit_sha': snap.commit_sha,
                    'commit_timestamp': snap.commit_timestamp,
                    'commit_subject': snap.commit_subject,
                    'indexed_at': snap.indexed_at,
                    'is_stale': is_stale,
                })

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

        context.update({
            'devices_data': devices_data,
            'missing': missing,
            'ok_count': ok_count,
            'stale_count': stale_count,
            'missing_count': len(missing),
            'total_count': len(devices_data),
            'stale_after_hours': stale_after_hours,
            'source': source,
        })
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

    def get(self, request, *args, **kwargs):
        # Single-source: send the "add" route to editing the existing source
        # rather than showing a create form that clean() would only reject.
        if 'pk' not in kwargs:
            existing = get_source()
            if existing:
                return redirect(
                    'plugins:netbox_oxidized_viewer:oxidizedsource_edit', pk=existing.pk
                )
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
    tab = ViewTab(
        label='Config History',
        permission='dcim.view_device',
        weight=500
    )

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
                error_message = f"No configuration backups found for this device (filename: {filename})."
        else:
            error_message = "No valid Oxidized Source mapping found for this device or Git repository is invalid."

        # Per-commit note counts, so the history table can show a badge without
        # a query per row.
        note_counts = {}
        if commits:
            for row in (
                models.ConfigCommitNote.objects
                .filter(device=instance, commit_sha__in=[c.sha for c in commits])
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
                models.ConfigCommitNote.objects
                .filter(device=instance, commit_sha=sha_new)
                .select_related('created_by')
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
        except GitBackendError:
            raise Http404
        return _config_attachment(content, f"{filename}.txt")


class CommitConfigDownloadView(View):
    def get(self, request, pk, sha):
        backend, filename = _device_backend_or_404(request, pk)
        try:
            content = backend.get_file_content(filename, sha)
        except GitBackendError:
            raise Http404
        return _config_attachment(content, f"{filename}-{sha[:7]}.txt")


class DiffDownloadView(View):
    def get(self, request, pk, sha_old, sha_new):
        backend, filename = _device_backend_or_404(request, pk)
        try:
            diff_data = backend.get_diff(filename, sha_old, sha_new)
        except GitBackendError:
            raise Http404

        lines = [f"--- a/{filename}", f"+++ b/{filename}"]
        for hunk in diff_data.hunks:
            lines.append(f"@@ -{hunk.old_start},{hunk.old_lines} +{hunk.new_start},{hunk.new_lines} @@")
            for marker, content in hunk.lines:
                lines.append(f"{marker}{content}")
        patch_text = "\n".join(lines) + "\n"

        return _config_attachment(
            patch_text,
            f"{filename}-{sha_old[:7]}-{sha_new[:7]}.patch",
            content_type='text/x-patch; charset=utf-8',
        )


class ConfigSearchView(LoginRequiredMixin, TemplateView):
    template_name = 'netbox_oxidized_viewer/search.html'

    # Rows rendered per page.  SearchHeadline is O(matches × content_size), so
    # we only ever annotate the rows on the requested page (see _fts_search).
    SEARCH_PAGE_SIZE = 25

    # SearchHeadline does not HTML-escape the content it returns, and config
    # content is device-controlled (banners, descriptions).  Have Postgres mark
    # matches with non-HTML sentinels, escape the whole headline, then swap the
    # sentinels for <mark> tags — never feed raw config through |safe.
    HEADLINE_START_SENTINEL = '\x01'
    HEADLINE_STOP_SENTINEL = '\x02'

    @classmethod
    def _render_headline(cls, raw_headline):
        escaped = escape(raw_headline)
        return mark_safe(
            escaped
            .replace(cls.HEADLINE_START_SENTINEL, '<mark>')
            .replace(cls.HEADLINE_STOP_SENTINEL, '</mark>')
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        query = self.request.GET.get('q', '').strip()
        results = []
        error = None
        total_count = 0
        page_obj = None

        if len(query) >= 2:
            source = get_source()
            if source:
                results, total_count, page_obj = self._fts_search(query, source)
            else:
                error = "No Oxidized source configured."

        context.update({
            'query': query,
            'results': results,
            'page_obj': page_obj,
            'error': error,
            'total_count': total_count,
            'page_size': self.SEARCH_PAGE_SIZE,
        })
        return context

    def _fts_search(self, query, source):
        sq = SearchQuery(query, config='simple')
        # Restrict to devices the requesting user has view permission for.
        allowed_devices = Device.objects.restrict(self.request.user, 'view')

        # Rank-ordered queryset of bare PKs — no headline, so COUNT(*) and the
        # OFFSET/LIMIT page slice stay cheap regardless of total match count.
        ranked_pks = (
            models.ConfigSnapshot.objects
            .filter(source=source, search_vector=sq, device__in=allowed_devices)
            # F('search_vector') references the stored tsvector column directly;
            # passing the bare name re-runs to_tsvector() on it and zeroes the rank.
            .annotate(rank=SearchRank(F('search_vector'), sq))
            .order_by('-rank', 'device_id')
            .values_list('pk', flat=True)
        )

        paginator = Paginator(ranked_pks, self.SEARCH_PAGE_SIZE)
        page_obj = paginator.get_page(self.request.GET.get('page') or 1)

        # Re-fetch only this page's rows with the (expensive) headline annotation,
        # preserving rank order.
        page_qs = (
            models.ConfigSnapshot.objects
            .filter(pk__in=list(page_obj.object_list))
            .annotate(
                rank=SearchRank(F('search_vector'), sq),
                headline=SearchHeadline(
                    'content', sq,
                    config='simple',
                    start_sel=self.HEADLINE_START_SENTINEL,
                    stop_sel=self.HEADLINE_STOP_SENTINEL,
                    max_words=50,
                    min_words=15,
                    max_fragments=3,
                    fragment_delimiter=' … ',
                ),
            )
            .select_related('device')
            .order_by('-rank', 'device_id')
        )

        results = [
            {
                'device': snap.device,
                'headline': self._render_headline(snap.headline),
                'commit_sha': snap.commit_sha,
                'indexed_at': snap.indexed_at,
            }
            for snap in page_qs
        ]
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
                device=device, commit_sha=sha, message=message, created_by=request.user,
            )
            messages.success(request, "Note added.")
        else:
            messages.warning(request, "Note was empty — nothing saved.")
        return redirect('plugins:netbox_oxidized_viewer:device_commit', pk=device.pk, sha_new=sha)


class DeviceSyncView(LoginRequiredMixin, View):
    """Trigger an on-demand Oxidized backup for a device (optional; requires the
    source's api_url). Read-only toward git — it just asks Oxidized to poll."""

    def post(self, request, pk):
        device = get_object_or_404(Device.objects.restrict(request.user, 'view'), pk=pk)
        source = get_source()
        if not source or not source.api_url:
            messages.error(request, "No Oxidized API URL is configured on the source.")
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
                "reindex or wait for the next cycle to see the new commit.",
            )
        except OxidizedAPIError as exc:
            messages.error(request, str(exc))
        return redirect('plugins:netbox_oxidized_viewer:device_oxidized_config', pk=device.pk)
