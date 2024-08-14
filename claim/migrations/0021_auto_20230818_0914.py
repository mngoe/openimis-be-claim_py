# Generated by Django 3.2.16 on 2023-08-18 09:14

from django.db import migrations, models
from claim.models import Claim

class Migration(migrations.Migration):

    dependencies = [
        ('claim', '0020_auto_20231102_1155'),
    ]

    operations = []
    
    try:
        Claim.objects.filter(pk__lt=10).aggregate(sum=models.Count('care_type'))
    except:
        operations = [
            migrations.AddField(
            model_name='claim',
            name='care_type',
            field=models.CharField(blank=True, db_column='CareType', max_length=4, null=True),
        )
    ]
