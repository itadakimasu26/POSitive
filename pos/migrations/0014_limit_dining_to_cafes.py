from decimal import Decimal

import django.core.validators
from django.db import migrations, models


def normalize_non_cafe_dining(apps, schema_editor):
    """Remove dining configuration and labels from non-Cafe stores.

    Historical monetary fields remain unchanged so previously completed sales
    retain their original accounting breakdown and audit value.
    """
    StoreSettings = apps.get_model("pos", "StoreSettings")
    Sale = apps.get_model("pos", "Sale")
    StoreSettings.objects.exclude(store_type="Cafe").update(service_charge_rate=0)
    Sale.objects.exclude(store__store_type="Cafe").update(order_type="Retail")


def restore_legacy_order_type(apps, schema_editor):
    Sale = apps.get_model("pos", "Sale")
    Sale.objects.filter(order_type="Retail").update(order_type="Take-out")


class Migration(migrations.Migration):
    dependencies = [
        ("pos", "0013_dining_service_charge_and_product_picture"),
    ]

    operations = [
        migrations.AlterField(
            model_name="sale",
            name="order_type",
            field=models.CharField(
                choices=[
                    ("Retail", "Retail sale"),
                    ("Dine-in", "Dine-in"),
                    ("Take-out", "Take-out"),
                ],
                default="Retail",
                max_length=12,
            ),
        ),
        migrations.AlterField(
            model_name="storesettings",
            name="service_charge_rate",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Cafe stores only. Percentage added to dine-in orders after discounts; take-out orders are not charged.",
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0.00")),
                    django.core.validators.MaxValueValidator(Decimal("100.00")),
                ],
            ),
        ),
        migrations.RunPython(normalize_non_cafe_dining, restore_legacy_order_type),
    ]
