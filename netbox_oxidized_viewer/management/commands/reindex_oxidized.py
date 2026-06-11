from django.core.management.base import BaseCommand

from netbox_oxidized_viewer.tasks import update_config_snapshots


class Command(BaseCommand):
    help = "Index (or re-index) Oxidized config snapshots for Postgres FTS."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            type=int,
            metavar="SOURCE_PK",
            help="Limit indexing to a single OxidizedSource by PK.",
        )

    def handle(self, *args, **options):
        source_pk = options.get("source")
        update_config_snapshots(source_pk=source_pk)
        self.stdout.write(self.style.SUCCESS("Indexing complete."))
