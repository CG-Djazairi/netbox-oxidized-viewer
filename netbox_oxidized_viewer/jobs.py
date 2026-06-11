"""
Background job that keeps the ConfigSnapshot full-text index fresh.

NetBox schedules background work with RQ (not Celery).  Registering a JobRunner
with @system_job makes NetBox enqueue it automatically on a fixed interval
whenever the RQ worker (`manage.py rqworker`) is running — no configuration.py
edits required.  The interval, in minutes, is configurable via the plugin's
`index_interval_minutes` setting (defaults to 60).
"""

import logging

from netbox.jobs import JobRunner, system_job
from netbox.plugins import get_plugin_config

from .tasks import update_config_snapshots

logger = logging.getLogger(__name__)

INDEX_INTERVAL_MINUTES = get_plugin_config(
    'netbox_oxidized_viewer', 'index_interval_minutes', default=60
)


@system_job(interval=INDEX_INTERVAL_MINUTES)
class ConfigSnapshotIndexJob(JobRunner):
    class Meta:
        name = "Update Oxidized config snapshots"

    def run(self, *args, **kwargs):
        update_config_snapshots()
