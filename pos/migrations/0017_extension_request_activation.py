from django.db import migrations, models


def close_older_open_requests(apps, schema_editor):
    ExtensionRequest = apps.get_model("pos", "SubscriptionExtensionRequest")
    database_alias = schema_editor.connection.alias
    open_requests = (
        ExtensionRequest.objects.using(database_alias)
        .filter(status__in=["New", "In review"])
        .order_by("store_id", "-created_at", "-pk")
    )
    newest_by_store = set()
    for extension_request in open_requests.iterator():
        if extension_request.store_id not in newest_by_store:
            newest_by_store.add(extension_request.store_id)
            continue
        migration_note = (
            "Automatically closed because a newer open request existed when duplicate "
            "protection was enabled."
        )
        existing_notes = extension_request.admin_notes.strip()
        extension_request.status = "Declined"
        extension_request.admin_notes = (
            f"{existing_notes}\n\n{migration_note}" if existing_notes else migration_note
        )
        extension_request.save(update_fields=["status", "admin_notes"])


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0016_subscriptionextensionrequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscriptionextensionrequest",
            name="activated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="subscriptionextensionrequest",
            name="extension_end",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="subscriptionextensionrequest",
            name="extension_start",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="subscriptionextensionrequest",
            name="status_email_error",
            field=models.CharField(blank=True, max_length=500),
        ),
        migrations.AddField(
            model_name="subscriptionextensionrequest",
            name="status_email_sent_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(close_older_open_requests, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="subscriptionextensionrequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ["New", "In review"])),
                fields=("store",),
                name="one_open_subscription_extension_request_per_store",
            ),
        ),
    ]
