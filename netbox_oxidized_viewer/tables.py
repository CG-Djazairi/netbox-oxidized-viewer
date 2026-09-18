import django_tables2 as tables
from netbox.tables import NetBoxTable, columns

from .models import OxidizedInventory, OxidizedSource


class OxidizedSourceTable(NetBoxTable):
    name = tables.Column(linkify=True)

    class Meta(NetBoxTable.Meta):
        model = OxidizedSource
        fields = ('pk', 'id', 'name', 'git_repo_path', 'node_name_source', 'inventory_ip_field', 'actions')
        default_columns = ('name', 'git_repo_path', 'node_name_source')


class OxidizedInventoryTable(NetBoxTable):
    name = tables.Column(linkify=True)
    enabled = columns.BooleanColumn()

    class Meta(NetBoxTable.Meta):
        model = OxidizedInventory
        fields = ('pk', 'id', 'name', 'slug', 'description', 'enabled', 'actions')
        default_columns = ('name', 'slug', 'description', 'enabled')
