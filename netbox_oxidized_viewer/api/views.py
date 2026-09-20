from dcim.models import Device
from django.shortcuts import get_object_or_404
from netbox.api.viewsets import NetBoxModelViewSet
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.routers import APIRootView
from rest_framework.views import APIView

from .. import filters
from ..health import find_device_for_node, record_run
from ..inventory import build_inventory
from ..models import ConfigCommitNote, OxidizedInventory, OxidizedSource
from ..permissions import CONFIG_VIEW_PERMISSION
from ..services.git_backend import GitBackendError
from ..utils import (
    get_backend_and_filename_for_device,
    get_source,
    resolve_device_field,
)
from .serializers import OxidizedInventorySerializer, OxidizedSourceSerializer


class OxidizedAPIRootView(APIRootView):
    """API root listing every endpoint of the plugin. DRF's default only lists the
    router-registered model endpoint (sources/), hiding the inventory and the
    per-device endpoints."""

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        namespace = request.resolver_match.namespace
        root = request.build_absolute_uri(request.path)
        response.data.update(
            {
                'inventory': reverse(f'{namespace}:inventory', request=request),
                'source': reverse(f'{namespace}:source-inventory', request=request),
                'devices/{pk}/config': f'{root}devices/{{pk}}/config/',
                'devices/{pk}/history': f'{root}devices/{{pk}}/history/',
                'devices/{pk}/diff/{sha_old}/{sha_new}': f'{root}devices/{{pk}}/diff/{{sha_old}}/{{sha_new}}/',
                'devices/{pk}/commits/{sha}/note': f'{root}devices/{{pk}}/commits/{{sha}}/note/',
                'devices/{pk}/sync': f'{root}devices/{{pk}}/sync/',
                'hook': reverse(f'{namespace}:hook', request=request),
            }
        )
        for slug in OxidizedInventory.objects.filter(enabled=True).values_list('slug', flat=True):
            response.data[f'inventory/{slug}'] = reverse(
                f'{namespace}:inventory-scoped', kwargs={'slug': slug}, request=request
            )
        return response


class OxidizedSourceViewSet(NetBoxModelViewSet):
    """CRUD for the Oxidized source (standard NetBox model endpoint)."""

    queryset = OxidizedSource.objects.prefetch_related('scope_roles', 'scope_platforms', 'scope_tags', 'tags')
    serializer_class = OxidizedSourceSerializer
    filterset_class = filters.OxidizedSourceFilterSet


class OxidizedInventoryViewSet(NetBoxModelViewSet):
    """CRUD for named (per-zone) inventories."""

    queryset = OxidizedInventory.objects.prefetch_related(
        'scope_sites', 'scope_roles', 'scope_platforms', 'scope_tags', 'tags'
    )
    serializer_class = OxidizedInventorySerializer
    filterset_class = filters.OxidizedInventoryFilterSet


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

    message = 'This endpoint requires the dcim.view_device permission.'

    def has_permission(self, request, view):
        return request.user.has_perm('dcim.view_device')


class CanViewConfigs(BasePermission):
    """
    The per-device endpoints serve configuration data: they need the plugin's
    own permission on top of view on the device (checked per object through
    Device.objects.restrict()). Deliberately not on the inventory or the hook,
    which Oxidized's token uses without any right to read configs.
    """

    message = f'This endpoint requires the {CONFIG_VIEW_PERMISSION} permission.'

    def has_permission(self, request, view):
        return request.user.has_perm(CONFIG_VIEW_PERMISSION)


class OxidizedHookView(APIView):
    """
    POST - Oxidized reports the outcome of a run (exec hook on node_success and
    node_fail). Body (form or JSON): event, node, and for failures status,
    err_type, err_reason. Requires the netbox_oxidized_viewer.add_backupstatus
    permission; the node must be a device the token's user may view.
    """

    permission_classes = [IsAuthenticated]
    # curl --data-urlencode is the only quoting-safe way to pass Oxidized's error
    # text from a shell hook; NetBox's API accepts JSON and multipart only by default.
    parser_classes = [JSONParser, FormParser, MultiPartParser]
    EVENTS = ('node_success', 'node_fail')

    def post(self, request):
        if not request.user.has_perm('netbox_oxidized_viewer.add_backupstatus'):
            return Response({'detail': 'Requires the netbox_oxidized_viewer.add_backupstatus permission.'}, status=403)
        source = get_source()
        if not source:
            return Response({'detail': 'No Oxidized source configured.'}, status=400)

        event = str(request.data.get('event') or '').strip()
        node = str(request.data.get('node') or '').strip()
        if event not in self.EVENTS or not node:
            return Response({'detail': f'event must be one of {self.EVENTS} and node is required.'}, status=400)

        device = find_device_for_node(source, node, Device.objects.restrict(request.user, 'view'))
        if device is None:
            return Response({'detail': f'No device matches node "{node}".'}, status=404)

        if event == 'node_success':
            status, error = 'success', ''
        else:
            status = str(request.data.get('status') or '').strip() or 'fail'
            if status == 'success':
                status = 'fail'
            parts = [str(request.data.get(key) or '').strip() for key in ('err_type', 'err_reason')]
            error = ': '.join(part for part in parts if part)
        record_run(device, status, error=error)
        return Response({'device': device.name, 'status': status}, status=201)


class OxidizedInventoryView(APIView):
    """
    Device inventory in Oxidized's HTTP source format.

    GET /api/plugins/oxidized-viewer/inventory/          every in-scope device
    GET /api/plugins/oxidized-viewer/inventory/<slug>/   one named inventory
                                                          (its scope AND the source scope)
    """

    permission_classes = [IsAuthenticated, CanViewDevices]

    def get(self, request, slug=None, *args, **kwargs):
        source = get_source()
        if not source:
            return Response([])
        inventory = None
        if slug is not None:
            inventory = get_object_or_404(OxidizedInventory.objects.all(), slug=slug, enabled=True)
        return Response(build_inventory(request.user, source, inventory))


# ---------------------------------------------------------------------------
# Read-only config API — same object-level RBAC as the UI download views.
# Automation (Ansible pre/post-change, CI, scripts) can pull configs through a
# NetBox token instead of Oxidized's unauthenticated REST API.
# ---------------------------------------------------------------------------


class _DeviceGitAPIView(APIView):
    permission_classes = [IsAuthenticated, CanViewConfigs]

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

        return Response(
            {
                'device_id': pk,
                'filename': filename,
                'commit_sha': sha,
                'content': content,
            }
        )


class DeviceHistoryAPIView(_DeviceGitAPIView):
    """GET the commit history for a device's config."""

    def get(self, request, pk):
        backend, filename = self._resolve(request, pk)
        if not backend:
            return Response({'detail': 'No Oxidized mapping for this device.'}, status=404)
        commits = backend.list_commits(filename, limit=100)
        return Response(
            {
                'device_id': pk,
                'filename': filename,
                'commits': [_commit_to_dict(c) for c in commits],
            }
        )


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

        return Response(
            {
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
            }
        )


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

    permission_classes = [IsAuthenticated, CanViewConfigs]

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
            device=device,
            commit_sha=sha,
            message=message,
            created_by=request.user,
        )
        return Response(_note_to_dict(note), status=201)


class DeviceSyncAPIView(APIView):
    """POST — trigger an on-demand Oxidized backup for a device (needs the
    source's api_url). Read-only toward git."""

    permission_classes = [IsAuthenticated, CanViewConfigs]

    def post(self, request, pk):
        # Triggering a backup is a write on an external system: require the
        # change permission on the device, not merely view.
        device = get_object_or_404(Device.objects.restrict(request.user, 'change'), pk=pk)
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
