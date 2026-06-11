import logging

from dcim.models import Device

from .models import ConfigSnapshot, OxidizedSource
from .services.git_backend import GitBackend, InvalidRepository, RepositoryNotFound
from .utils import resolve_device_field

logger = logging.getLogger(__name__)


def update_config_snapshots(source_pk=None):
    """
    Walk all OxidizedSource instances (or a single one when source_pk is given),
    compare the latest git commit SHA per device against the stored snapshot,
    and upsert ConfigSnapshot rows only when the content has changed.

    Triggered by:
      - The ConfigSnapshotIndexJob system job, which NetBox runs on a schedule
        via the RQ worker (see jobs.py).
      - Manually via: python manage.py reindex_oxidized
    """
    sources = (
        OxidizedSource.objects.filter(pk=source_pk)
        if source_pk
        else OxidizedSource.objects.all()
    )

    for source in sources:
        try:
            backend = GitBackend(source.git_repo_path)
        except (RepositoryNotFound, InvalidRepository) as exc:
            logger.warning("Skipping source '%s': %s", source.name, exc)
            continue

        updated = skipped = errors = 0

        for device in Device.objects.iterator():
            filename = resolve_device_field(device, source.node_name_source)
            if not filename:
                continue

            latest = backend.get_latest_commit(filename)
            if not latest:
                continue

            existing = ConfigSnapshot.objects.filter(device=device).first()
            if existing and existing.commit_sha == latest.sha:
                skipped += 1
                continue

            try:
                content = backend.get_file_content(filename, latest.sha)
            except Exception as exc:
                logger.warning(
                    "Could not fetch content for %s @ %s: %s",
                    filename, latest.sha[:7], exc,
                )
                errors += 1
                continue

            ConfigSnapshot.objects.update_or_create(
                device=device,
                defaults={
                    'source': source,
                    'content': content,
                    'commit_sha': latest.sha,
                },
            )
            updated += 1

        logger.info(
            "Source '%s': %d updated, %d skipped (unchanged), %d errors.",
            source.name, updated, skipped, errors,
        )
