from django.conf import settings
from django.db import migrations

class Migration(migrations.Migration):

    dependencies = [
        ('claim', '0032_merge_20240318_1324'),
    ]

    operations = [
        migrations.RunSQL("DELETE FROM [tblRoleRight] WHERE RightID = 111007 AND RoleID = (SELECT RoleID FROM [tblRole] WHERE [RoleName] = 'Claim Administrator' AND [ValidityTo] is null)"
                          if settings.MSSQL else
                          """DELETE FROM "tblRoleRight" WHERE "RightID" = 111007 AND "RoleID" = (SELECT "RoleID" FROM "tblRole" WHERE "RoleName" = 'Claim Administrator' AND "ValidityTo" is null)""")
    ]