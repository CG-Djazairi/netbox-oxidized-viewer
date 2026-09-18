import logging

from dcim.models import Device

from .health import pull_statuses
from .models import ConfigSnapshot, OxidizedSource
from .services.git_backend import GitBackend, InvalidRepository, RepositoryNotFound
from .services.oxidized_api import OxidizedAPIError
from .utils import resolve_device_field, scope_device_queryset

logger = logging.getLogger(__name__)


def update_config_snapshots(source_pk=None):
    """
    Walk all OxidizedSource instances (or a single one when source_pk is given),
    compare the latest git commit SHA per device against the stored snapshot,
    and upsert ConfigSnapshot rows only when the content has changed.

    The repository history is walked ONCE per source (latest_commit_per_file),
    not once per device, so cost is O(history) rather than O(devices × history).

    Triggered by:
      - The ConfigSnapshotIndexJob system job, which NetBox runs on a schedule
        via the RQ worker (see jobs.py).
      - Manually via: python manage.py reindex_oxidized
    """
    sources = OxidizedSource.objects.filter(pk=source_pk) if source_pk else OxidizedSource.objects.all()

    for source in sources:
        try:
            backend = GitBackend(source.git_repo_path)
        except (RepositoryNotFound, InvalidRepository) as exc:
            logger.warning("Skipping source '%s': %s", source.name, exc)
            continue

        # Single history walk: {filename: CommitMeta} for every file at HEAD.
        latest_by_file = backend.latest_commit_per_file()

        # Prefetch existing snapshots so the per-device loop does not issue a
        # query each iteration (ConfigSnapshot is one row per device).
        existing = {
            snap.device_id: snap for snap in ConfigSnapshot.objects.only('device_id', 'commit_sha', 'commit_timestamp')
        }

        updated = skipped = errors = 0
        kept_device_ids = []  # devices that still have a file in the repo

        # Only index devices in this source's scope (roles/platforms/tags).
        scoped_devices = scope_device_queryset(source, Device.objects.all())
        for device in scoped_devices.iterator():
            filename = resolve_device_field(device, source.node_name_source)
            if not filename:
                continue

            latest = latest_by_file.get(filename)
            if not latest:
                continue
            kept_device_ids.append(device.id)

            prev = existing.get(device.id)
            # commit_timestamp check backfills rows indexed before the commit
            # metadata columns existed (migration 0006).
            if prev and prev.commit_sha == latest.sha and prev.commit_timestamp:
                skipped += 1
                continue

            try:
                content = backend.get_file_content(filename, latest.sha)
            except Exception as exc:
                logger.warning(
                    'Could not fetch content for %s @ %s: %s',
                    filename,
                    latest.sha[:7],
                    exc,
                )
                errors += 1
                continue

            ConfigSnapshot.objects.update_or_create(
                device=device,
                defaults={
                    'source': source,
                    'content': content,
                    'commit_sha': latest.sha,
                    'commit_timestamp': latest.timestamp,
                    'commit_subject': latest.subject[:255],
                },
            )
            updated += 1

        # A device whose file disappeared from the repo (or that left the scope)
        # must not keep showing a healthy backup: drop its snapshot.
        removed, _ = ConfigSnapshot.objects.filter(source=source).exclude(device_id__in=kept_device_ids).delete()

        logger.info(
            "Source '%s': %d updated, %d skipped (unchanged), %d removed (no file), %d errors.",
            source.name,
            updated,
            skipped,
            removed,
            errors,
        )

        # Run outcomes (success/failure per device) when Oxidized's web API is
        # configured; installs without it report through the exec hook instead.
        if source.api_url:
            try:
                reported = pull_statuses(source)
                logger.info("Source '%s': run status refreshed for %d devices.", source.name, reported)
            except OxidizedAPIError as exc:
                logger.warning("Source '%s': could not read run status from Oxidized: %s", source.name, exc)
