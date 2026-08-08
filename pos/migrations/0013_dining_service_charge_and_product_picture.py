from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):

    dependencies = [
        ("pos", "0012_storesettings_store_type_alter_product_category_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="picture_url",
            field=models.URLField(
                blank=True,
                help_text="Optional hosted product photo. A name-based placeholder is used when this is blank.",
                max_length=500,
            ),
        ),
        migrations.AddField(
            model_name="sale",
            name="order_type",
            field=models.CharField(
                choices=[("Dine-in", "Dine-in"), ("Take-out", "Take-out")],
                default="Take-out",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="sale",
            name="service_charge",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AddField(
            model_name="sale",
            name="service_charge_rate",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=5),
        ),
        migrations.AddField(
            model_name="storesettings",
            name="service_charge_rate",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Percentage added to dine-in orders after discounts. Take-out orders are not charged.",
                max_digits=5,
                validators=[MinValueValidator(Decimal("0.00")), MaxValueValidator(Decimal("100.00"))],
            ),
        ),
        migrations.AddConstraint(
            model_name="storesettings",
            constraint=models.CheckConstraint(
                condition=Q(service_charge_rate__gte=0, service_charge_rate__lte=100),
                name="store_service_charge_rate_valid",
            ),
        ),
    ]
