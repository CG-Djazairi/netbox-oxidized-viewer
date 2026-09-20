"""
Device-page integration: a small "Config Backup" card on the core device detail
page showing backup freshness at a glance, without opening the Config History tab.

All data comes from the denormalized ConfigSnapshot columns (one cheap query,
no git access) — the OneToOne reverse accessor `device.oxidized_snapshot`.
"""

from netbox.plugins import PluginTemplateExtension, get_plugin_config

from .health import compute_health
from .permissions import CONFIG_VIEW_PERMISSION
from .utils import get_source


class DeviceBackupStatus(PluginTemplateExtension):
    models = ['dcim.device']

    def right_page(self):
        # Only surface the card when the plugin is actually configured.
        if not get_source():
            return ''

        device = self.context.get('object')
        if device is None:
            return ''

        # The card shows commit metadata: same permission as the Config History tab.
        request = self.context.get('request')
        if request is None or not request.user.has_perm(CONFIG_VIEW_PERMISSION):
            return ''

        # Reverse OneToOne raises a DoesNotExist that subclasses AttributeError,
        # so getattr(..., None) is the idiomatic "may be absent" access.
        snapshot = getattr(device, 'oxidized_snapshot', None)

        stale_after = get_plugin_config('netbox_oxidized_viewer', 'stale_after_hours') or 26
        run_status = getattr(device, 'oxidized_backup_status', None)
        status = compute_health(snapshot, run_status, stale_after)

        return self.render(
            'netbox_oxidized_viewer/inc/device_backup_card.html',
            extra_context={
                'oxi_snapshot': snapshot,
                'oxi_status': status,
                'oxi_run': run_status,
                'oxi_stale_after_hours': stale_after,
            },
        )


template_extensions = [DeviceBackupStatus]
