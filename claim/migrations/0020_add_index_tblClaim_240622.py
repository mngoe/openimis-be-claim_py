# Generated by Django 3.2.23 on 2024-01-30 11:53

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('claim', '0019_auto_20230918_2158'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='claim',
            index=models.Index(fields=['code', 'uuid', 'status'], name='IX_tblClaim_code_insuree_uuid_status'),
        )
    ]
