from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('netbox_oxidized_viewer', '0003_configsnapshot'),
    ]

    operations = [
        migrations.DeleteModel(name='ConfigBookmark'),
    ]
