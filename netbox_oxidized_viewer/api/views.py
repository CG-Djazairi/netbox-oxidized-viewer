from django.shortcuts import get_object_or_404
from netbox.plugins import get_plugin_config
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from dcim.models import Device

from ..models import ConfigCommitNote
from ..services.git_backend import GitBackendError
from ..utils import (
    get_backend_and_filename_for_device,
    get_source,
    resolve_device_field,
    scope_device_queryset,
)


def _commit_to_dict(commit):
    return {
        'sha': commit.sha,
        'author_name': commit.author_name,
        'author_email': commit.author_email,
        'subject': commit.subject,
        'body': commit.body,
        'timestamp': commit.timestamp,
    }


class CanViewDevices(BasePermission):
    """
    The inventory is a full device/IP listing — gate it behind the standard
    NetBox device-view permission rather than bare authentication.
    """
    message = "This endpoint requires the dcim.view_device permission."

    def has_permission(self, request, view):
        return request.user.has_perm('dcim.view_device')


def _oxidized_model(device, platform_map):
    """Return the Oxidized driver name for a device.

    Oxidized's `model` field is a *driver* name (ios, eos, junos…), which maps
    to NetBox's platform, not the hardware model. Prefer platform.slug (with an
    optional override map for when the slug differs from the driver name); fall
    back to the device_type model only when no platform is set.
    """
    if device.platform:
        slug = device.platform.slug
        return platform_map.get(slug, slug)
    if device.device_type:
        return device.device_type.model.lower()
    return "unknown"


class OxidizedInventoryView(APIView):
    """
    Returns the device inventory in Oxidized's HTTP source format.
    Endpoint: GET /api/plugins/oxidized-viewer/source/
    """
    permission_classes = [IsAuthenticated, CanViewDevices]

    def get(self, request, *args, **kwargs):
        source = get_source()
        if not source:
            return Response([])

        platform_map = get_plugin_config('netbox_oxidized_viewer', 'platform_model_map') or {}
        group_field = get_plugin_config('netbox_oxidized_viewer', 'inventory_group_field')

        # restrict() honours ObjectPermission constraints, so a token scoped to
        # a subset of devices only ever exports that subset. scope filters keep
        # out-of-scope gear (passive devices, servers, …) out of the node list
        # entirely, so Oxidized never tries to poll them.
        devices = (
            Device.objects.restrict(request.user, 'view')
            .filter(status='active')
            .select_related('device_type', 'platform', 'primary_ip4')
        )
        devices = scope_device_queryset(source, devices)

        inventory = []
        for device in devices:
            name = resolve_device_field(device, source.node_name_source)
            if not name:
                continue
            entry = {
                "name": name,
                "model": _oxidized_model(device, platform_map),
                "ip": str(device.primary_ip4.address.ip) if device.primary_ip4 else "",
            }
            if group_field:
                group = resolve_device_field(device, group_field)
                if group:
                    entry["group"] = group
            inventory.append(entry)

        return Response(inventory)


# ---------------------------------------------------------------------------
# Read-only config API — same object-level RBAC as the UI download views.
# Automation (Ansible pre/post-change, CI, scripts) can pull configs through a
# NetBox token instead of Oxidized's unauthenticated REST API.
# ---------------------------------------------------------------------------

class _DeviceGitAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def _resolve(self, request, pk):
        """(backend, filename) for an RBAC-restricted device, or None."""
        device = get_object_or_404(Device.objects.restrict(request.user, 'view'), pk=pk)
        backend, filename = get_backend_and_filename_for_device(device)
        if not backend:
            return None, None
        return backend, filename


class DeviceConfigAPIView(_DeviceGitAPIView):
    """GET latest config (or ?sha=<commit>) for a device as JSON."""

    def get(self, request, pk):
        backend, filename = self._resolve(request, pk)
        if not backend:
            return Response({'detail': 'No Oxidized mapping for this device.'}, status=404)

        sha = request.query_params.get('sha')
        if not sha:
            latest = backend.get_latest_commit(filename)
            if not latest:
                return Response({'detail': 'No configuration backups found.'}, status=404)
            sha = latest.sha
        try:
            content = backend.get_file_content(filename, sha)
        except GitBackendError as exc:
            return Response({'detail': str(exc)}, status=404)

        return Response({
            'device_id': pk,
            'filename': filename,
            'commit_sha': sha,
            'content': content,
        })


class DeviceHistoryAPIView(_DeviceGitAPIView):
    """GET the commit history for a device's config."""

    def get(self, request, pk):
        backend, filename = self._resolve(request, pk)
        if not backend:
            return Response({'detail': 'No Oxidized mapping for this device.'}, status=404)
        commits = backend.list_commits(filename, limit=100)
        return Response({
            'device_id': pk,
            'filename': filename,
            'commits': [_commit_to_dict(c) for c in commits],
        })


class DeviceDiffAPIView(_DeviceGitAPIView):
    """GET a structured diff between two commits of a device's config."""

    def get(self, request, pk, sha_old, sha_new):
        backend, filename = self._resolve(request, pk)
        if not backend:
            return Response({'detail': 'No Oxidized mapping for this device.'}, status=404)
        try:
            diff = backend.get_diff(filename, sha_old, sha_new)
        except GitBackendError as exc:
            return Response({'detail': str(exc)}, status=404)

        return Response({
            'device_id': pk,
            'filename': filename,
            'old_sha': diff.old_sha,
            'new_sha': diff.new_sha,
            'hunks': [
                {
                    'old_start': h.old_start,
                    'old_lines': h.old_lines,
                    'new_start': h.new_start,
                    'new_lines': h.new_lines,
                    'lines': [{'marker': m, 'content': c} for m, c in h.lines],
                }
                for h in diff.hunks
            ],
        })


def _note_to_dict(note):
    return {
        'id': note.pk,
        'commit_sha': note.commit_sha,
        'message': note.message,
        'created_by': note.created_by.username if note.created_by else None,
        'created': note.created,
    }


class DeviceCommitNoteAPIView(APIView):
    """
    GET  — list notes attached to a device's commit.
    POST — attach a note ({"message": "..."}) to that commit. Enables the
           automation loop: make a change, resync, then annotate the new SHA.
    Notes live in NetBox, not git.
    """
    permission_classes = [IsAuthenticated]

    def _device(self, request, pk):
        return get_object_or_404(Device.objects.restrict(request.user, 'view'), pk=pk)

    def get(self, request, pk, sha):
        device = self._device(request, pk)
        notes = ConfigCommitNote.objects.filter(device=device, commit_sha=sha)
        return Response([_note_to_dict(n) for n in notes])

    def post(self, request, pk, sha):
        device = self._device(request, pk)
        if not request.user.has_perm('netbox_oxidized_viewer.add_configcommitnote'):
            return Response(
                {'detail': 'Requires the netbox_oxidized_viewer.add_configcommitnote permission.'},
                status=403,
            )
        message = (request.data.get('message') or '').strip()
        if not message:
            return Response({'detail': 'message is required.'}, status=400)
        note = ConfigCommitNote.objects.create(
            device=device, commit_sha=sha, message=message, created_by=request.user,
        )
        return Response(_note_to_dict(note), status=201)


class DeviceSyncAPIView(APIView):
    """POST — trigger an on-demand Oxidized backup for a device (needs the
    source's api_url). Read-only toward git."""
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        device = get_object_or_404(Device.objects.restrict(request.user, 'view'), pk=pk)
        source = get_source()
        if not source or not source.api_url:
            return Response({'detail': 'No Oxidized API URL is configured on the source.'}, status=400)
        node = resolve_device_field(device, source.node_name_source)
        if not node:
            return Response({'detail': "Could not resolve this device's node name."}, status=400)

        from ..services.oxidized_api import OxidizedAPIError, trigger_backup
        try:
            trigger_backup(source.api_url, node)
        except OxidizedAPIError as exc:
            return Response({'detail': str(exc)}, status=502)
        return Response({'status': 'requested', 'node': node})
