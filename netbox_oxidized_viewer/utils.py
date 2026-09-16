from .models import OxidizedSource
from .services.cache import CachedGitBackend
from .services.git_backend import GitBackend, InvalidRepository, RepositoryNotFound


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
      cf_<name>  →  the custom field value; an object custom field pointing at an
                    IP address resolves to the bare address (no prefix length)

    Returns a string or None if the field is missing/empty.
    """
    if not field_expr:
        return None

    if field_expr.startswith('cf_'):
        cf_name = field_expr[3:]
        # device.cf deserializes object-type custom fields into model instances
        # (e.g. an IPAddress); custom_field_data only holds the raw pk. Fall back
        # to the raw value when no CustomField definition exists for the name.
        try:
            deserialized = device.cf
        except Exception:
            deserialized = {}
        if cf_name in deserialized:
            value = deserialized[cf_name]
        else:
            value = device.custom_field_data.get(cf_name)
    else:
        value = getattr(device, field_expr, None)

    # IP address proxy objects expose .address.ip
    if value is not None and hasattr(value, 'address'):
        return str(value.address.ip)

    return str(value) if value is not None else None


def scope_device_queryset(source, queryset):
    """
    Narrow a Device queryset to the devices in this source's Oxidized scope.

    A device is in scope when it matches every scope filter that is set on the
    source (roles AND platforms AND tags). An unset filter adds no constraint, so
    a source with no scope configured returns the queryset unchanged.
    """
    if source is None:
        return queryset
    if source.scope_roles.exists():
        queryset = queryset.filter(role__in=source.scope_roles.all())
    if source.scope_platforms.exists():
        queryset = queryset.filter(platform__in=source.scope_platforms.all())
    if source.scope_tags.exists():
        queryset = queryset.filter(tags__in=source.scope_tags.all()).distinct()
    return queryset


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
