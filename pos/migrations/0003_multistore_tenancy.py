import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


def prepare_existing_data(apps, schema_editor):
    Store = apps.get_model("pos", "StoreSettings")
    Product = apps.get_model("pos", "Product")
    Sale = apps.get_model("pos", "Sale")
    SubscriptionRequest = apps.get_model("pos", "SubscriptionRequest")
    StoreMembership = apps.get_model("pos", "StoreMembership")
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))

    stores = list(Store.objects.order_by("pk"))
    if not stores:
        return

    used_ids = set()
    for store in stores:
        base = (store.store_id or f"STORE-{store.pk}").strip().upper()
        candidate = base[:30]
        suffix = 2
        while candidate in used_ids:
            marker = f"-{suffix}"
            candidate = f"{base[:30 - len(marker)]}{marker}"
            suffix += 1
        if store.store_id != candidate:
            Store.objects.filter(pk=store.pk).update(store_id=candidate)
            store.store_id = candidate
        used_ids.add(candidate)

    default_store = stores[0]
    Product.objects.filter(store__isnull=True).update(store=default_store)
    Sale.objects.filter(store__isnull=True).update(store=default_store)
    SubscriptionRequest.objects.filter(store__isnull=True).update(store=default_store)

    existing_users = list(User.objects.filter(is_active=True, is_superuser=False).order_by("pk"))
    administrator_assigned = False
    for user in existing_users:
        role = "Staff"
        if user.is_staff and not administrator_assigned:
            role = "Administrator"
            administrator_assigned = True
        StoreMembership.objects.get_or_create(
            store=default_store,
            user=user,
            defaults={"role": role, "active": True},
        )


class Migration(migrations.Migration):
    dependencies = [("pos", "0002_saleitem_unit_cost_and_validation")]

    operations = [
        migrations.AddField(
            model_name="storesettings",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="storesettings",
            name="status",
            field=models.CharField(
                choices=[("Active", "Active"), ("Suspended", "Suspended")],
                default="Active",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="storesettings",
            name="subscription_end",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="storesettings",
            name="subscription_start",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="storesettings",
            name="store_id",
            field=models.CharField(max_length=30),
        ),
        migrations.AddField(
            model_name="product",
            name="store",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="products",
                to="pos.storesettings",
            ),
        ),
        migrations.AddField(
            model_name="sale",
            name="store",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sales",
                to="pos.storesettings",
            ),
        ),
        migrations.AddField(
            model_name="subscriptionrequest",
            name="store",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="legacy_subscription_requests",
                to="pos.storesettings",
            ),
        ),
        migrations.CreateModel(
            name="StoreMembership",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("role", models.CharField(choices=[("Administrator", "Store administrator"), ("Staff", "Staff")], default="Staff", max_length=20)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("store", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memberships", to="pos.storesettings")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="store_memberships", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["store__business_name", "role", "user__username"]},
        ),
        migrations.RunPython(prepare_existing_data, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="storesettings",
            name="store_id",
            field=models.CharField(max_length=30, unique=True),
        ),
        migrations.AlterField(
            model_name="product",
            name="barcode",
            field=models.CharField(blank=True, max_length=50),
        ),
        migrations.AlterField(
            model_name="product",
            name="store",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="products", to="pos.storesettings"),
        ),
        migrations.AlterField(
            model_name="sale",
            name="store",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sales", to="pos.storesettings"),
        ),
        migrations.AlterField(
            model_name="subscriptionrequest",
            name="store",
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="legacy_subscription_requests", to="pos.storesettings"),
        ),
        migrations.AlterModelOptions(
            name="storesettings",
            options={"ordering": ["business_name", "store_id"], "verbose_name": "store", "verbose_name_plural": "stores"},
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.UniqueConstraint(fields=("store", "barcode"), name="unique_store_barcode"),
        ),
        migrations.AddConstraint(
            model_name="storemembership",
            constraint=models.UniqueConstraint(fields=("store", "user"), name="unique_store_user_membership"),
        ),
        migrations.AddConstraint(
            model_name="storemembership",
            constraint=models.UniqueConstraint(
                condition=Q(active=True, role="Administrator"),
                fields=("store",),
                name="one_active_admin_per_store",
            ),
        ),
    ]
