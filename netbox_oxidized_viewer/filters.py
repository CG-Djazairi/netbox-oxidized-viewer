from netbox.filtersets import NetBoxModelFilterSet
from .models import OxidizedSource

class OxidizedSourceFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = OxidizedSource
        fields = ('id', 'name')
