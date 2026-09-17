"""URL converter for git commit SHAs, so malformed values never reach the git
layer or the database (they 404 at routing instead of raising a 500)."""

from django.urls import converters


class ShaConverter:
    regex = '[0-9a-fA-F]{7,40}'

    def to_python(self, value):
        return value.lower()

    def to_url(self, value):
        return value


def register():
    if 'sha' not in converters.get_converters():
        converters.register_converter(ShaConverter, 'sha')
