import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0015_remove_subscriptionrequest"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SubscriptionExtensionRequest",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "requested_plan",
                    models.CharField(
                        choices=[("Starter", "Starter"), ("Pro", "Pro")],
                        max_length=20,
                    ),
                ),
                (
                    "payment_type",
                    models.CharField(
                        choices=[
                            ("GCash", "GCash"),
                            ("Maya / card", "Maya / card"),
                            ("Bank transfer", "Bank transfer"),
                            ("Cash", "Cash"),
                        ],
                        max_length=30,
                    ),
                ),
                ("comments", models.TextField(blank=True, max_length=1000)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("New", "New"),
                            ("In review", "In review"),
                            ("Completed", "Completed"),
                            ("Declined", "Declined"),
                        ],
                        default="New",
                        max_length=20,
                    ),
                ),
                ("admin_notes", models.TextField(blank=True)),
                ("email_sent_at", models.DateTimeField(blank=True, null=True)),
                ("email_error", models.CharField(blank=True, max_length=500)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "requested_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="subscription_extension_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "reviewed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="reviewed_subscription_extension_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "store",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="subscription_extension_requests",
                        to="pos.storesettings",
                    ),
                ),
            ],
            options={
                "verbose_name": "subscription extension request",
                "verbose_name_plural": "subscription extension requests",
                "ordering": ["-created_at"],
            },
        ),
    ]
