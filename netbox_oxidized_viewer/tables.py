import django_tables2 as tables
from netbox.tables import NetBoxTable

from .models import OxidizedSource


class OxidizedSourceTable(NetBoxTable):
    name = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = OxidizedSource
        fields = ('pk', 'id', 'name', 'git_repo_path', 'node_name_source', 'inventory_ip_field', 'actions')
        default_columns = ('name', 'git_repo_path', 'node_name_source')
