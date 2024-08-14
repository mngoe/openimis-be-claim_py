# Generated by Django 3.2.23 on 2024-01-30 11:53

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('claim', '0025_auto_20231205_1455'),
    ]

    operations = [
        migrations.AddField(
            model_name='claim',
            name='tdr',
            field=models.BooleanField(blank=True, db_column='TDRResult', null=True),
        ),
        migrations.AddField(
            model_name='claim',
            name='test_number',
            field=models.CharField(blank=True, db_column='TestNumber', max_length=255, null=True),
        ),
    ]
