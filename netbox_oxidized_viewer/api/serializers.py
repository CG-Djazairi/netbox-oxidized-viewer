"""
REST API serializer for OxidizedSource.

Required beyond the REST endpoint itself: OxidizedSource is a NetBoxModel, so
every save from the UI goes through NetBox's change-log / event-rule pipeline,
which serializes the object with ``<app>.api.serializers.<Model>Serializer``.
Without this class, saving a source raises ``SerializerNotFound``.
"""

from dcim.api.serializers import DeviceRoleSerializer, PlatformSerializer, SiteSerializer
from dcim.models import DeviceRole, Platform, Site
from extras.api.serializers import TagSerializer
from extras.models import Tag
from netbox.api.fields import SerializedPKRelatedField
from netbox.api.serializers import NetBoxModelSerializer

from ..models import OxidizedInventory, OxidizedSource


class OxidizedSourceSerializer(NetBoxModelSerializer):
    scope_roles = SerializedPKRelatedField(
        queryset=DeviceRole.objects.all(), serializer=DeviceRoleSerializer, nested=True, required=False, many=True
    )
    scope_platforms = SerializedPKRelatedField(
        queryset=Platform.objects.all(), serializer=PlatformSerializer, nested=True, required=False, many=True
    )
    scope_tags = SerializedPKRelatedField(
        queryset=Tag.objects.all(), serializer=TagSerializer, nested=True, required=False, many=True
    )

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


class OxidizedInventorySerializer(NetBoxModelSerializer):
    scope_sites = SerializedPKRelatedField(
        queryset=Site.objects.all(), serializer=SiteSerializer, nested=True, required=False, many=True
    )
    scope_roles = SerializedPKRelatedField(
        queryset=DeviceRole.objects.all(), serializer=DeviceRoleSerializer, nested=True, required=False, many=True
    )
    scope_platforms = SerializedPKRelatedField(
        queryset=Platform.objects.all(), serializer=PlatformSerializer, nested=True, required=False, many=True
    )
    scope_tags = SerializedPKRelatedField(
        queryset=Tag.objects.all(), serializer=TagSerializer, nested=True, required=False, many=True
    )

    class Meta:
        model = OxidizedInventory
        fields = (
            'id',
            'url',
            'display_url',
            'display',
            'name',
            'slug',
            'description',
            'enabled',
            'scope_sites',
            'scope_roles',
            'scope_platforms',
            'scope_tags',
            'tags',
            'custom_fields',
            'created',
            'last_updated',
        )
        brief_fields = ('id', 'url', 'display', 'name', 'slug')
