from django import template

register = template.Library()


@register.filter
def dictkey(mapping, key):
    """Look up a dict value by a variable key (Django templates can't do this
    natively). Returns None if the mapping is falsy or the key is absent."""
    if not mapping:
        return None
    return mapping.get(key)
