from netbox.forms import NetBoxModelForm, NetBoxModelFilterSetForm
from .models import OxidizedSource

class OxidizedSourceForm(NetBoxModelForm):
    class Meta:
        model = OxidizedSource
        fields = ('name', 'git_repo_path', 'node_name_source', 'tags')

class OxidizedSourceFilterForm(NetBoxModelFilterSetForm):
    model = OxidizedSource
    # Basic filters can be added here if needed in the UI
