import django.contrib.postgres.indexes
import django.contrib.postgres.search
import django.db.models.deletion
from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):

    # AddIndexConcurrently cannot run inside a transaction.
    # atomic=False lets Postgres build the GIN index without an exclusive table lock,
    # so this migration is safe to run on a live production database.
    atomic = False

    dependencies = [
        ('dcim', '0227_alter_interface_speed_bigint'),
        ('netbox_oxidized_viewer', '0002_flexible_node_name_source'),
    ]

    operations = [
        migrations.CreateModel(
            name='ConfigSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('content', models.TextField()),
                ('commit_sha', models.CharField(max_length=40)),
                ('indexed_at', models.DateTimeField(auto_now=True)),
                ('search_vector', django.contrib.postgres.search.SearchVectorField(null=True)),
                ('device', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='oxidized_snapshot',
                    to='dcim.device',
                )),
                ('source', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='snapshots',
                    to='netbox_oxidized_viewer.oxidizedsource',
                )),
            ],
        ),
        AddIndexConcurrently(
            model_name='configsnapshot',
            index=django.contrib.postgres.indexes.GinIndex(
                fields=['search_vector'],
                name='netbox_oxi_cfgsnapshot_sv_gin',
            ),
        ),
    ]
