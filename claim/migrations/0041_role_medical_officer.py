from django.db import migrations

from core.models import Role, RoleRight
from core.utils import insert_role_right_for_system

MEDICAL_OFFICER_ID = 16
CLAIM_ADMIN = 256

CLAIM_VALIDATE_ADMIN_HF_PRE_AUTH_PERMS = ["111015"]
CLAIM_VALIDATE_MEDICAL_PRE_AUTH_PERMS = ["111016"]
CLAIM_REJECT_PRE_AUTH_PERMS = ["111018"]




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
    _add_rights_to_role(MEDICAL_OFFICER_ID,CLAIM_VALIDATE_MEDICAL_PRE_AUTH_PERMS)
    _add_rights_to_role(CLAIM_ADMIN,CLAIM_VALIDATE_ADMIN_HF_PRE_AUTH_PERMS)
    _add_rights_to_role(MEDICAL_OFFICER_ID,CLAIM_REJECT_PRE_AUTH_PERMS)
    _add_rights_to_role(CLAIM_ADMIN,CLAIM_REJECT_PRE_AUTH_PERMS)


def on_migration_reverse(apps, schema_editor):
    _remove_rights_from_role(MEDICAL_OFFICER_ID,CLAIM_VALIDATE_MEDICAL_PRE_AUTH_PERMS)
    _remove_rights_from_role(CLAIM_ADMIN,CLAIM_VALIDATE_ADMIN_HF_PRE_AUTH_PERMS)
    _remove_rights_from_role(MEDICAL_OFFICER_ID,CLAIM_REJECT_PRE_AUTH_PERMS)
    _remove_rights_from_role(CLAIM_ADMIN,CLAIM_REJECT_PRE_AUTH_PERMS)


class Migration(migrations.Migration):

    dependencies = [
        ('claim', '0040_claim_audit_user_id_pre_auth_claim_care_type_and_more'),
    ]

    operations = [
        migrations.RunPython(on_migration, on_migration_reverse)
    ]