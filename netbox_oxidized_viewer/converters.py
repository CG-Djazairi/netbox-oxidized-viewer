"""URL converter for git commit SHAs, so malformed values never reach the git
layer or the database (they 404 at routing instead of raising a 500)."""

import re

from django.urls import converters


def normalize_sha(value):
    """Validate query parameters by the same rule as SHA path parameters."""
    if not re.fullmatch(ShaConverter.regex, value):
        raise ValueError('Commit SHA must contain exactly 40 hexadecimal characters.')
    return value.lower()


class ShaConverter:
    # Full SHAs only: the git layer looks objects up by exact id and never
    # expands abbreviations, so a short value could only ever 404 later.
    regex = '[0-9a-fA-F]{40}'

    def to_python(self, value):
        return value.lower()

    def to_url(self, value):
        return value


def register():
    if 'sha' not in converters.get_converters():
        converters.register_converter(ShaConverter, 'sha')
