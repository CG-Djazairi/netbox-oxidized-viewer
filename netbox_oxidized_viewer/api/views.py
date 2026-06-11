from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from dcim.models import Device

from ..utils import get_source, resolve_device_field


class OxidizedInventoryView(APIView):
    """
    Returns the device inventory in Oxidized's HTTP source format.
    Endpoint: GET /api/plugins/oxidized-viewer/source/
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        source = get_source()
        if not source:
            return Response([])

        inventory = []
        for device in Device.objects.filter(status='active').select_related('device_type', 'primary_ip4'):
            name = resolve_device_field(device, source.node_name_source)
            if not name:
                continue
            inventory.append({
                "name": name,
                "model": device.device_type.model.lower() if device.device_type else "unknown",
                "ip": str(device.primary_ip4.address.ip) if device.primary_ip4 else "",
            })

        return Response(inventory)
