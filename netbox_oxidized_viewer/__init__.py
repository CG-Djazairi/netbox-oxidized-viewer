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
        # A device whose newest backup is older than this is flagged "stale" on
        # the dashboard and the device Backup Status card. Default 26h covers a
        # daily backup cycle with slack.
        'stale_after_hours': 26,
        # Inventory endpoint: map a NetBox platform slug to the Oxidized driver
        # name when they differ (e.g. {'cisco-ios': 'ios', 'arista-eos': 'eos'}).
        # When a device has a platform not in the map, its slug is exported as-is;
        # when it has no platform, the device_type model is used (legacy behaviour).
        'platform_model_map': {},
        # Optional device attribute exported as the Oxidized "group" (for
        # group-based credentials). e.g. 'site', 'tenant', 'role', or 'cf_<name>'.
        # None omits the group field entirely.
        'inventory_group_field': None,

        # --- Optional zero-touch install: auto-provision the OxidizedSource ---
        # Set git_repo_path here and the source is created automatically on
        # `migrate` (create-if-absent), so a fresh deployment needs no UI step.
        # Leave it blank to create/manage the source in the UI instead (the
        # default). Once a source exists, the UI is authoritative — changing
        # these settings never overwrites it.
        'git_repo_path': '',
        'source_name': 'default',
        'node_name_source': 'name',
        'api_url': '',
    }

    def ready(self):
        from django.db.models.signals import post_migrate
        from . import navigation  # noqa: F401
        from . import jobs  # noqa: F401  registers the ConfigSnapshotIndexJob system job
        from .bootstrap import post_migrate_provision
        # Auto-provision the source from settings after this app's migrations.
        post_migrate.connect(post_migrate_provision, sender=self)
        super().ready()


config = OxidizedViewerConfig
