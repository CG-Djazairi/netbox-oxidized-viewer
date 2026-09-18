from netbox.filtersets import NetBoxModelFilterSet

from .models import OxidizedInventory, OxidizedSource


class OxidizedSourceFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = OxidizedSource
        fields = ('id', 'name')


class OxidizedInventoryFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = OxidizedInventory
        fields = ('id', 'name', 'slug', 'enabled')
