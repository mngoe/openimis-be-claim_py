from django.db import migrations

from core.models import Role, RoleRight
from core.utils import insert_role_right_for_system

CLAIM_ADMIN_ID = 256
IMIS_ADMIN = 64

CLAIM_RESTORE_RIGHT = ["111012"]


def _get_role(role_id):
    return Role.objects.filter(is_system=role_id).first()


def _add_rights_to_role(role):
    for right in CLAIM_RESTORE_RIGHT:
        insert_role_right_for_system(role, right)


def _remove_rights_from_role(role):
    RoleRight.objects.filter(
        role__is_system=role,
        right_id__in=CLAIM_RESTORE_RIGHT,
        validity_to__isnull=True
    ).delete()


def on_migration(apps, schema_editor):
    _add_rights_to_role(IMIS_ADMIN)
    _add_rights_to_role(CLAIM_ADMIN_ID)


def on_migration_reverse(apps, schema_editor):
    _remove_rights_from_role(IMIS_ADMIN)
    _remove_rights_from_role(CLAIM_ADMIN_ID)


class Migration(migrations.Migration):

    dependencies = [
        ('claim', '0023_claim_restore'),
    ]

    operations = [
        migrations.RunPython(on_migration, on_migration_reverse)
    ]
