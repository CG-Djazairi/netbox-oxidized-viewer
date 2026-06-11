from .models import OxidizedSource
from .services.git_backend import GitBackend, RepositoryNotFound, InvalidRepository
from .services.cache import CachedGitBackend


def get_source():
    """
    Return the single configured OxidizedSource, or None.

    The plugin is single-source by design (enforced in OxidizedSource.clean);
    this is the one place that lookup lives so callers never assume row order.
    """
    return OxidizedSource.objects.first()


def resolve_device_field(device, field_expr: str):
    """
    Resolve a field expression against a Device instance.

    Supported forms:
      name, serial, asset_tag, primary_ip4, primary_ip6, ... (any device attr)
      cf_<name>  →  device.custom_field_data['<name>']

    Returns a string or None if the field is missing/empty.
    """
    if not field_expr:
        return None

    if field_expr.startswith('cf_'):
        cf_name = field_expr[3:]
        value = device.custom_field_data.get(cf_name)
        return str(value) if value is not None else None

    value = getattr(device, field_expr, None)

    # IP address proxy objects expose .address.ip
    if value is not None and hasattr(value, 'address'):
        return str(value.address.ip)

    return str(value) if value is not None else None


def get_backend_and_filename_for_device(device):
    """
    Returns (CachedGitBackend, filename) for a given device, or (None, None).
    """
    source = get_source()
    if not source:
        return None, None

    filename = resolve_device_field(device, source.node_name_source)
    if not filename:
        return None, None

    try:
        git_backend = GitBackend(source.git_repo_path)
        return CachedGitBackend(git_backend), filename
    except (RepositoryNotFound, InvalidRepository):
        return None, None
