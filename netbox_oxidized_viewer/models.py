from django.conf import settings
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import GeneratedField, Value
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from netbox.models import NetBoxModel
from netbox.models.features import JobsMixin


class OxidizedSource(JobsMixin, NetBoxModel):
    # JobsMixin: NetBox only lets a Job be attached to object types with the
    # 'jobs' feature; the 'Reindex now' job is attached to the source.
    name = models.CharField(max_length=100, unique=True, help_text=_('A unique name for this Oxidized source'))
    git_repo_path = models.CharField(
        max_length=255,
        help_text=_('Path to the bare git repository inside the NetBox container (e.g., /opt/oxidized-git)'),
    )
    node_name_source = models.CharField(
        max_length=100,
        default='name',
        help_text=_(
            'Device field used to match the Oxidized node name. '
            'Use any device attribute (e.g. name, serial, asset_tag, primary_ip4) '
            'or a custom field prefixed with cf_ (e.g. cf_oxidized_name). '
            "Defaults to 'name' (device hostname)."
        ),
    )
    api_url = models.URLField(
        blank=True,
        default='',
        help_text=_(
            'Optional Oxidized REST API base URL (e.g. http://oxidized:8888), used '
            'only to trigger on-demand backups from NetBox. The plugin still reads '
            "history from git — this never replaces the git repo. Oxidized's API is "
            'unauthenticated, so restrict network access to it.'
        ),
    )
    inventory_ip_field = models.CharField(
        max_length=100,
        blank=True,
        default='primary_ip4',
        verbose_name=_('Inventory IP field'),
        help_text=_(
            'Device field exported as the Oxidized "ip" by the inventory endpoint: any device '
            'attribute (default primary_ip4) or a custom field prefixed with cf_ '
            '(e.g. cf_management_interface). An object custom field pointing at an IP address '
            'exports the bare address.'
        ),
    )
    # Scope: which devices are in Oxidized's remit. A device is in scope when it
    # matches every filter that is set (roles AND platforms AND tags); an empty
    # filter adds no constraint, so leaving all three blank = every active device
    # (the pre-scope behaviour). This keeps out passive gear, servers, etc. from
    # both the exported inventory (so Oxidized never polls them) and the
    # "never backed up" dashboard list.
    scope_roles = models.ManyToManyField(
        to='dcim.DeviceRole',
        blank=True,
        related_name='+',
        help_text=_('Only back up devices with these roles (blank = any role).'),
    )
    scope_platforms = models.ManyToManyField(
        to='dcim.Platform',
        blank=True,
        related_name='+',
        help_text=_('Only back up devices with these platforms (blank = any platform).'),
    )
    scope_tags = models.ManyToManyField(
        to='extras.Tag',
        blank=True,
        related_name='+',
        help_text=_('Only back up devices carrying at least one of these tags (blank = any).'),
    )

    class Meta:
        ordering = ('name',)
        verbose_name = _('Oxidized Source')
        verbose_name_plural = _('Oxidized Sources')
        constraints = [
            # The plugin is intentionally single-source: every read path resolves
            # the active source via get_source() (= objects.first()). A unique
            # index on a constant expression permits exactly one row, so a second
            # source cannot be created even by paths that skip clean() (nbshell,
            # scripts, or two form submissions racing the exists() check).
            models.UniqueConstraint(Value(True), name='netbox_oxi_single_source'),
        ]

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('plugins:netbox_oxidized_viewer:oxidizedsource', args=[self.pk])

    def clean(self):
        super().clean()
        # Mirror the DB-level singleton constraint with a friendly form error
        # (the constraint alone would surface as an IntegrityError 500).
        if OxidizedSource.objects.exclude(pk=self.pk).exists():
            raise ValidationError(
                _('Only one Oxidized source can be configured. Edit the existing source instead of adding another.')
            )


class ConfigSnapshot(models.Model):
    """
    One row per device — stores the most-recently-indexed config content and a
    Postgres tsvector for full-text search.  Populated exclusively by the
    update_config_snapshots indexing job; never written by user-facing code.
    """

    # TODO: revisit if multi-source becomes a real use case (currently one source per device)
    device = models.OneToOneField(
        to='dcim.Device',
        on_delete=models.CASCADE,
        related_name='oxidized_snapshot',
    )
    source = models.ForeignKey(
        to='OxidizedSource',
        on_delete=models.CASCADE,
        related_name='snapshots',
    )
    content = models.TextField()
    commit_sha = models.CharField(max_length=40)
    # Denormalized from the indexed commit so list views (dashboard) never have
    # to walk git history — git is only opened for content and diffs.
    commit_timestamp = models.DateTimeField(null=True, blank=True)
    commit_subject = models.CharField(max_length=255, blank=True, default='')
    indexed_at = models.DateTimeField(auto_now=True)
    # STORED generated column: Postgres recomputes the tsvector from content on
    # every write, so the FTS index can never drift from the content — no signal,
    # and bulk_create/queryset.update() stay correct too. 'simple' config: no
    # stemming (see docs/decisions/0004-search-index.md).
    search_vector = GeneratedField(
        expression=SearchVector('content', config='simple'),
        output_field=SearchVectorField(),
        db_persist=True,
    )

    class Meta:
        # Name pinned to match the initial migration so Django never tries to
        # rename the production index.
        indexes = [GinIndex(fields=['search_vector'], name='netbox_oxi_cfgsnapshot_sv_gin')]

    def __str__(self):
        return f'Snapshot({self.device_id}, {self.commit_sha[:7]})'


class ConfigCommitNote(models.Model):
    """
    A user- or automation-authored annotation attached to a specific config
    commit (by SHA). Oxidized's own commit messages are generic; these notes
    record *why* a config changed.

    Stored in NetBox, NOT written into the git repo — the plugin stays read-only
    toward Oxidized's repository, and notes work even for historical commits
    (whose git messages are immutable).
    """

    device = models.ForeignKey(
        to='dcim.Device',
        on_delete=models.CASCADE,
        related_name='oxidized_commit_notes',
    )
    commit_sha = models.CharField(max_length=40)
    message = models.TextField()
    created_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='+',
    )
    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('created',)
        indexes = [models.Index(fields=['device', 'commit_sha'])]

    def __str__(self):
        return f'Note on {self.device_id}@{self.commit_sha[:7]}'
