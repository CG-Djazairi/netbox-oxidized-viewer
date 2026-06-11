from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVector, SearchVectorField
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from netbox.models import NetBoxModel


class OxidizedSource(NetBoxModel):
    name = models.CharField(
        max_length=100, 
        unique=True,
        help_text=_("A unique name for this Oxidized source")
    )
    git_repo_path = models.CharField(
        max_length=255, 
        help_text=_("Path to the bare git repository inside the NetBox container (e.g., /opt/oxidized-git)")
    )
    node_name_source = models.CharField(
        max_length=100,
        default='name',
        help_text=_(
            "Device field used to match the Oxidized node name. "
            "Use any device attribute (e.g. name, serial, asset_tag, primary_ip4) "
            "or a custom field prefixed with cf_ (e.g. cf_oxidized_name). "
            "Defaults to 'name' (device hostname)."
        )
    )

    class Meta:
        ordering = ('name',)
        verbose_name = _('Oxidized Source')
        verbose_name_plural = _('Oxidized Sources')

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('plugins:netbox_oxidized_viewer:oxidizedsource', args=[self.pk])

    def clean(self):
        super().clean()
        # The plugin is intentionally single-source: every read path resolves
        # the active source via OxidizedSource.objects.first().  Guard against a
        # second row so that lookup stays unambiguous.
        if OxidizedSource.objects.exclude(pk=self.pk).exists():
            raise ValidationError(
                _("Only one Oxidized source can be configured. "
                  "Edit the existing source instead of adding another.")
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
    search_vector = SearchVectorField(null=True)

    class Meta:
        # Name pinned to match migration 0003 (AddIndexConcurrently) so Django
        # never tries to rename the production index.
        indexes = [GinIndex(fields=['search_vector'], name='netbox_oxi_cfgsnapshot_sv_gin')]

    def __str__(self):
        return f"Snapshot({self.device_id}, {self.commit_sha[:7]})"


@receiver(post_save, sender=ConfigSnapshot)
def _update_search_vector(sender, instance, **kwargs):
    # Use filter().update() rather than instance.save() to avoid re-triggering
    # this signal.  'simple' config: no stemming — network configs are
    # identifier-heavy, not English prose (see docs/decisions/0004-search-index.md).
    ConfigSnapshot.objects.filter(pk=instance.pk).update(
        search_vector=SearchVector('content', config='simple')
    )