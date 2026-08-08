from django.db import migrations
from django.utils import timezone


def require_password_change_for_existing_store_users(apps, schema_editor):
    User = apps.get_model("auth", "User")
    StoreMembership = apps.get_model("pos", "StoreMembership")
    UserSecurityProfile = apps.get_model("pos", "UserSecurityProfile")

    assigned_user_ids = StoreMembership.objects.filter(
        active=True,
        user__is_active=True,
        user__is_superuser=False,
    ).values_list("user_id", flat=True).distinct()
    required_at = timezone.now()
    for user_id in User.objects.filter(pk__in=assigned_user_ids).values_list("pk", flat=True):
        UserSecurityProfile.objects.update_or_create(
            user_id=user_id,
            defaults={
                "must_change_password": True,
                "password_change_required_at": required_at,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("pos", "0004_usersecurityprofile"),
    ]

    operations = [
        migrations.RunPython(
            require_password_change_for_existing_store_users,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
