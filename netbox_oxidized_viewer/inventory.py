"""
Builds the device list in Oxidized's HTTP-source format.

Used by the API endpoints (fleet-wide and per-inventory) and by the inventory
page in the UI (preview). Permissions are applied through
Device.objects.restrict(): a token can never see more than its user may view.
"""

from dcim.models import Device
from netbox.plugins import get_plugin_config

from .utils import resolve_device_field, scope_device_queryset


def oxidized_model(device, platform_map):
    """
    Oxidized driver name for a device: the platform slug (through the optional
    override map for when the slug differs from the driver name); fall back to
    the device_type model only when no platform is set.
    """
    if device.platform:
        slug = device.platform.slug
        return platform_map.get(slug, slug)
    if device.device_type:
        return device.device_type.model.lower()
    return 'unknown'


def inventory_devices(user, source, inventory=None):
    """Active devices the user may view, within the source scope and, when
    given, the inventory's own scope."""
    devices = (
        Device.objects.restrict(user, 'view')
        .filter(status='active')
        .select_related('device_type', 'platform', 'primary_ip4', 'site')
    )
    devices = scope_device_queryset(source, devices)
    if inventory is not None:
        devices = scope_device_queryset(inventory, devices)
    return devices


def build_inventory(user, source, inventory=None):
    """[{name, model, ip[, group]}, ...] for Oxidized's http source."""
    if source is None:
        return []
    platform_map = get_plugin_config('netbox_oxidized_viewer', 'platform_model_map') or {}
    group_field = get_plugin_config('netbox_oxidized_viewer', 'inventory_group_field')
    ip_field = source.inventory_ip_field or 'primary_ip4'

    entries = []
    for device in inventory_devices(user, source, inventory):
        name = resolve_device_field(device, source.node_name_source)
        if not name:
            continue
        entry = {
            'name': name,
            'model': oxidized_model(device, platform_map),
            'ip': resolve_device_field(device, ip_field) or '',
        }
        if group_field:
            group = resolve_device_field(device, group_field)
            if group:
                entry['group'] = group
        entries.append(entry)
    return entries
