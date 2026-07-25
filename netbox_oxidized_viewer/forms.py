from dcim.models import DeviceRole, Platform
from django import forms
from extras.models import Tag
from netbox.forms import NetBoxModelFilterSetForm, NetBoxModelForm
from utilities.forms.fields import DynamicModelMultipleChoiceField
from utilities.forms.rendering import FieldSet

from .models import OxidizedSource
from .services.git_backend import GitBackend, InvalidRepository, RepositoryNotFound


class OxidizedSourceForm(NetBoxModelForm):
    scope_roles = DynamicModelMultipleChoiceField(
        queryset=DeviceRole.objects.all(),
        required=False,
        label='Device roles',
        help_text='Only back up devices with these roles (blank = any).',
    )
    scope_platforms = DynamicModelMultipleChoiceField(
        queryset=Platform.objects.all(),
        required=False,
        label='Platforms',
        help_text='Only back up devices with these platforms (blank = any).',
    )
    scope_tags = DynamicModelMultipleChoiceField(
        queryset=Tag.objects.all(),
        required=False,
        label='Device tags',
        help_text='Only back up devices with at least one of these tags (blank = any).',
    )

    fieldsets = (
        FieldSet('name', 'git_repo_path', 'node_name_source', 'api_url', name='Source'),
        FieldSet('scope_roles', 'scope_platforms', 'scope_tags', name='Backup scope'),
        FieldSet('tags', name='Tags'),
    )

    class Meta:
        model = OxidizedSource
        fields = (
            'name',
            'git_repo_path',
            'node_name_source',
            'api_url',
            'scope_roles',
            'scope_platforms',
            'scope_tags',
            'tags',
        )

    def clean_git_repo_path(self):
        """Fail fast at save time if the path is not a readable git repository.

        Without this, a typo saves cleanly and only surfaces later as
        "No valid Oxidized Source mapping found" on every device tab and as a
        silently-skipped indexing job.
        """
        path = self.cleaned_data['git_repo_path']
        try:
            GitBackend(path)
        except RepositoryNotFound as exc:
            raise forms.ValidationError(
                f'Path not found inside the NetBox container: {path}. '
                'Check that the Oxidized git repository is mounted here.'
            ) from exc
        except InvalidRepository as exc:
            raise forms.ValidationError(
                f'{path} exists but is not a git repository. '
                'Point this at the bare Oxidized git repo (the directory containing HEAD).'
            ) from exc
        return path


class OxidizedSourceFilterForm(NetBoxModelFilterSetForm):
    model = OxidizedSource
    # Basic filters can be added here if needed in the UI
