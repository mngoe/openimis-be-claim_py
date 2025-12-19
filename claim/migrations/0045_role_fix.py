from django.db import migrations

from core.models import Role, RoleRight
from core.utils import insert_role_right_for_system

MEDICAL_OFFICER_ID = 16
CLAIM_CONTRIBUTOR=512

CLAIM_SUBMIT_PREAUTH= ["111019"]
CLAIM_READ=["111001"]
CLAIM_LOAD=["111005"]


def _add_rights_to_role(role,rightArray):
    for right in rightArray:
        insert_role_right_for_system(role, right)


def _remove_rights_from_role(role,rightArray):
    RoleRight.objects.filter(
        role__is_system=role,
        right_id__in=rightArray,
        validity_to__isnull=True
    ).delete()


def on_migration(apps, schema_editor):
    _add_rights_to_role(CLAIM_CONTRIBUTOR,CLAIM_SUBMIT_PREAUTH)
    _add_rights_to_role(MEDICAL_OFFICER_ID,CLAIM_READ)
    _add_rights_to_role(MEDICAL_OFFICER_ID,CLAIM_LOAD)


def on_migration_reverse(apps, schema_editor):
    _remove_rights_from_role(CLAIM_CONTRIBUTOR,CLAIM_SUBMIT_PREAUTH)
    _remove_rights_from_role(MEDICAL_OFFICER_ID,CLAIM_READ)
    _remove_rights_from_role(MEDICAL_OFFICER_ID,CLAIM_LOAD)



class Migration(migrations.Migration):

    dependencies = [
        ('claim', '0044_claim_icd_nullable'),
    ]

    operations = [
        migrations.RunPython(on_migration, on_migration_reverse)
    ]