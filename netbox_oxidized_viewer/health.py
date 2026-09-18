"""
Backup health: is a device being backed up, as opposed to "did its config change
recently".

Two facts are combined:
  * ConfigSnapshot.commit_timestamp - when the configuration last changed;
  * BackupStatus - the outcome of Oxidized's latest run, when Oxidized reports it
    (exec hook -> /api/plugins/oxidized-viewer/hook/, or nodes.json pulled by the
    index job when the source has an API URL).
"""

import datetime
import logging

from dcim.models import Device
from django.utils import timezone

from .models import BackupStatus
from .services.oxidized_api import fetch_nodes
from .utils import resolve_device_field, scope_device_queryset

logger = logging.getLogger(__name__)

SUCCESS = 'success'

OK = 'ok'  # latest run succeeded recently (or, with no run reports, the config changed recently)
FAILING = 'failing'  # Oxidized reported that the latest run failed
STALE = 'stale'  # Oxidized used to report runs for this device and stopped
UNVERIFIED = 'unverified'  # no run reports at all and an old commit: unchanged or broken, unknown
MISSING = 'missing'  # no configuration in the repository


def compute_health(snapshot, status, stale_after_hours, now=None):
    """Health state for a device from its snapshot (may be None) and BackupStatus (may be None)."""
    now = now or timezone.now()
    window = datetime.timedelta(hours=stale_after_hours)

    if status is not None:
        if status.last_status != SUCCESS:
            return FAILING
        if now - status.last_run > window:
            return STALE
        return OK if snapshot is not None else MISSING

    if snapshot is None:
        return MISSING
    if snapshot.commit_timestamp and now - snapshot.commit_timestamp <= window:
        return OK
    return UNVERIFIED


def find_device_for_node(source, node, queryset):
    """The device whose node name (per the source's node_name_source) equals `node`."""
    field = source.node_name_source or 'name'
    if field.startswith('cf_'):
        return queryset.filter(**{f'custom_field_data__{field[3:]}': node}).first()
    if field in ('name', 'serial', 'asset_tag'):
        return queryset.filter(**{field: node}).first()
    for device in scope_device_queryset(source, queryset).iterator():
        if resolve_device_field(device, field) == node:
            return device
    return None


def record_run(device, status, when=None, error=''):
    """Store the outcome of a run. Older reports never overwrite newer ones."""
    when = when or timezone.now()
    existing = BackupStatus.objects.filter(device=device).first()
    if existing and existing.last_run > when:
        return existing
    succeeded = status == SUCCESS
    defaults = {
        'last_status': status[:50],
        'last_run': when,
        'last_error': '' if succeeded else (error or '')[:255],
    }
    if succeeded:
        defaults['last_success'] = when
    obj, _ = BackupStatus.objects.update_or_create(device=device, defaults=defaults)
    return obj


def _parse_oxidized_time(value):
    # "2026-09-18 13:13:13 UTC"
    try:
        parsed = datetime.datetime.strptime(value, '%Y-%m-%d %H:%M:%S %Z')
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=datetime.UTC)


def pull_statuses(source):
    """Refresh BackupStatus from Oxidized's nodes.json. Returns the number of devices updated.
    Raises OxidizedAPIError when the API cannot be reached."""
    by_name = {node.get('name'): node for node in fetch_nodes(source.api_url)}
    updated = 0
    for device in scope_device_queryset(source, Device.objects.filter(status='active')).iterator():
        node = by_name.get(resolve_device_field(device, source.node_name_source))
        last = (node or {}).get('last') or {}
        when = _parse_oxidized_time(last.get('end'))
        if not when or not last.get('status'):
            continue
        record_run(device, str(last['status']), when=when)
        updated += 1
    return updated
