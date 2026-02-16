from django.db import migrations

from core.utils import insert_role_right_for_system

CLAIM_ADMIN_ID = 256
IMIS_ADMIN = 64

CLAIM_RESTORE_RIGHT = ["111012"]


def _add_rights_to_role(role, apps):
    for right in CLAIM_RESTORE_RIGHT:
        insert_role_right_for_system(role, right, apps)


def _remove_rights_from_role(role, apps):
    RoleRight = apps.get_model("core", "RoleRight")
    RoleRight.objects.filter(
        role__is_system=role, right_id__in=CLAIM_RESTORE_RIGHT, validity_to__isnull=True
    ).delete()


def on_migration(apps, schema_editor):
    _add_rights_to_role(IMIS_ADMIN, apps)
    _add_rights_to_role(CLAIM_ADMIN_ID, apps)


def on_migration_reverse(apps, schema_editor):
    _remove_rights_from_role(IMIS_ADMIN, apps)
    _remove_rights_from_role(CLAIM_ADMIN_ID, apps)


class Migration(migrations.Migration):

    dependencies = [
        ("claim", "0023_claim_restore"),
    ]

    operations = [migrations.RunPython(on_migration, on_migration_reverse)]
