"""
REST API serializer for OxidizedSource.

Required beyond the REST endpoint itself: OxidizedSource is a NetBoxModel, so
every save from the UI goes through NetBox's change-log / event-rule pipeline,
which serializes the object with ``<app>.api.serializers.<Model>Serializer``.
Without this class, saving a source raises ``SerializerNotFound``.
"""

from dcim.api.serializers import DeviceRoleSerializer, PlatformSerializer
from extras.api.serializers import TagSerializer
from netbox.api.serializers import NetBoxModelSerializer

from ..models import OxidizedSource


class OxidizedSourceSerializer(NetBoxModelSerializer):
    scope_roles = DeviceRoleSerializer(nested=True, many=True, required=False)
    scope_platforms = PlatformSerializer(nested=True, many=True, required=False)
    scope_tags = TagSerializer(nested=True, many=True, required=False)

    class Meta:
        model = OxidizedSource
        fields = (
            'id',
            'url',
            'display_url',
            'display',
            'name',
            'git_repo_path',
            'node_name_source',
            'inventory_ip_field',
            'api_url',
            'scope_roles',
            'scope_platforms',
            'scope_tags',
            'tags',
            'custom_fields',
            'created',
            'last_updated',
        )
        brief_fields = ('id', 'url', 'display', 'name')
