from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_oxidized_viewer', '0004_remove_configbookmark'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='oxidizedsource',
            name='api_url',
        ),
    ]
