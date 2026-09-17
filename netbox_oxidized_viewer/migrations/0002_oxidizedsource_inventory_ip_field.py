from django.db import migrations, models


def seed_from_settings(apps, schema_editor):
    """Existing sources keep working: copy the PLUGINS_CONFIG value (the only way
    to set the IP field before this migration) onto the row."""
    from netbox.plugins import get_plugin_config

    configured = get_plugin_config('netbox_oxidized_viewer', 'inventory_ip_field')
    if configured and configured != 'primary_ip4':
        OxidizedSource = apps.get_model('netbox_oxidized_viewer', 'OxidizedSource')
        OxidizedSource.objects.update(inventory_ip_field=configured)


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_oxidized_viewer', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='oxidizedsource',
            name='inventory_ip_field',
            field=models.CharField(blank=True, default='primary_ip4', max_length=100),
        ),
        migrations.RunPython(seed_from_settings, migrations.RunPython.noop),
    ]
