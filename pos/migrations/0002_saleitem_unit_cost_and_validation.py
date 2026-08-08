from decimal import Decimal

import django.core.validators
from django.db import migrations, models
from django.db.models import Q


def backfill_unit_cost(apps, schema_editor):
    Product = apps.get_model("pos", "Product")
    SaleItem = apps.get_model("pos", "SaleItem")
    costs = dict(Product.objects.values_list("pk", "cost"))
    for product_id, cost in costs.items():
        SaleItem.objects.filter(product_id=product_id).update(unit_cost=cost)


class Migration(migrations.Migration):
    dependencies = [("pos", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="saleitem",
            name="unit_cost",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
        migrations.RunPython(backfill_unit_cost, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="product",
            name="cost",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=10,
                validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
            ),
        ),
        migrations.AlterField(
            model_name="product",
            name="price",
            field=models.DecimalField(
                decimal_places=2,
                max_digits=10,
                validators=[django.core.validators.MinValueValidator(Decimal("0.00"))],
            ),
        ),
        migrations.AlterField(
            model_name="storesettings",
            name="tax_rate",
            field=models.DecimalField(
                decimal_places=2,
                default=12,
                max_digits=5,
                validators=[
                    django.core.validators.MinValueValidator(Decimal("0.00")),
                    django.core.validators.MaxValueValidator(Decimal("100.00")),
                ],
            ),
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.CheckConstraint(condition=Q(cost__gte=0), name="product_cost_gte_zero"),
        ),
        migrations.AddConstraint(
            model_name="product",
            constraint=models.CheckConstraint(condition=Q(price__gte=0), name="product_price_gte_zero"),
        ),
        migrations.AddConstraint(
            model_name="storesettings",
            constraint=models.CheckConstraint(
                condition=Q(tax_rate__gte=0, tax_rate__lte=100),
                name="store_tax_rate_valid",
            ),
        ),
    ]
