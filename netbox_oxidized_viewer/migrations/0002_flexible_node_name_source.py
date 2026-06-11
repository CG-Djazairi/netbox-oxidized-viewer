from django.db import migrations, models


def migrate_mapping_rule(apps, schema_editor):
    OxidizedSource = apps.get_model('netbox_oxidized_viewer', 'OxidizedSource')
    for source in OxidizedSource.objects.all():
        source.node_name_source = 'primary_ip4' if source.device_mapping_rule == 'ip' else 'name'
        source.save(update_fields=['node_name_source'])


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_oxidized_viewer', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='oxidizedsource',
            name='node_name_source',
            field=models.CharField(
                default='name',
                max_length=100,
                help_text=(
                    "Device field used to match the Oxidized node name. "
                    "Use any device attribute (e.g. name, serial, asset_tag, primary_ip4) "
                    "or a custom field prefixed with cf_ (e.g. cf_oxidized_name). "
                    "Defaults to 'name' (device hostname)."
                ),
            ),
        ),
        migrations.RunPython(migrate_mapping_rule, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='oxidizedsource',
            name='device_mapping_rule',
        ),
    ]
