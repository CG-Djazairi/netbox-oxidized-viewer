"""
Optional zero-touch provisioning of the OxidizedSource from PLUGINS_CONFIG.

When ``git_repo_path`` is set in the plugin config, a source is created
automatically after migrations, so a fresh install needs no manual UI step.
Semantics:

* Opt-in — nothing happens unless ``git_repo_path`` is configured.
* Create-if-absent — if a source already exists (from settings or the UI), the
  UI stays authoritative and settings never overwrite it.
"""

import logging
import sys

from netbox.plugins import get_plugin_config

logger = logging.getLogger('netbox_oxidized_viewer')

PLUGIN = 'netbox_oxidized_viewer'


def provision_source_from_settings():
    """Create the single OxidizedSource from settings if configured and absent.

    Returns the created source, or None when disabled / a source already exists.
    """
    from .models import OxidizedSource

    repo_path = get_plugin_config(PLUGIN, 'git_repo_path')
    if not repo_path:
        return None  # opt-in: no bootstrap unless a path is configured
    if OxidizedSource.objects.exists():
        return None  # UI/existing source wins — never clobber

    source = OxidizedSource.objects.create(
        name=get_plugin_config(PLUGIN, 'source_name') or 'default',
        git_repo_path=repo_path,
        node_name_source=get_plugin_config(PLUGIN, 'node_name_source') or 'name',
        api_url=get_plugin_config(PLUGIN, 'api_url') or '',
    )
    logger.info(
        "Auto-provisioned OxidizedSource '%s' from PLUGINS_CONFIG (git_repo_path=%s)",
        source.name, repo_path,
    )
    return source


def post_migrate_provision(sender, **kwargs):
    """post_migrate receiver — bootstrap the source on real migrations only."""
    # Never seed the throwaway test database created by `manage.py test`.
    if 'test' in sys.argv:
        return
    try:
        provision_source_from_settings()
    except Exception as exc:  # never let bootstrap break `migrate`
        logger.warning("Oxidized source auto-provision skipped: %s", exc)
