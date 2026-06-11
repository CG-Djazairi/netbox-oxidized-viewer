from netbox.plugins import PluginConfig


class OxidizedViewerConfig(PluginConfig):
    name = 'netbox_oxidized_viewer'
    verbose_name = 'Oxidized Config Viewer'
    description = 'View Oxidized configuration backups with diff, history, and search'
    version = '0.1.0'
    base_url = 'oxidized-viewer'
    min_version = '4.5.0'

    default_settings = {
        # Minutes between automatic re-indexing runs of the FTS snapshot table.
        'index_interval_minutes': 60,
    }

    def ready(self):
        from . import navigation  # noqa: F401
        from . import jobs  # noqa: F401  registers the ConfigSnapshotIndexJob system job
        super().ready()


config = OxidizedViewerConfig
