from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from dcim.models import Device

from ..utils import get_source, resolve_device_field


class CanViewDevices(BasePermission):
    """
    The inventory is a full device/IP listing — gate it behind the standard
    NetBox device-view permission rather than bare authentication.
    """
    message = "This endpoint requires the dcim.view_device permission."

    def has_permission(self, request, view):
        return request.user.has_perm('dcim.view_device')


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

        # restrict() honours ObjectPermission constraints, so a token scoped to
        # a subset of devices only ever exports that subset.
        devices = (
            Device.objects.restrict(request.user, 'view')
            .filter(status='active')
            .select_related('device_type', 'primary_ip4')
        )

        inventory = []
        for device in devices:
            name = resolve_device_field(device, source.node_name_source)
            if not name:
                continue
            inventory.append({
                "name": name,
                "model": device.device_type.model.lower() if device.device_type else "unknown",
                "ip": str(device.primary_ip4.address.ip) if device.primary_ip4 else "",
            })

        return Response(inventory)
