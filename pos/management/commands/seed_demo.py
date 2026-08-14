from django.core.management.base import BaseCommand

from pos.models import Product, StoreSettings


PRODUCTS = [
    {"name": "Iced Spanish Latte", "category": "Drinks", "barcode": "POS-DRINK-001", "cost": 72, "price": 145, "stock": 28},
    {"name": "Classic Milk Tea", "category": "Drinks", "barcode": "POS-DRINK-002", "cost": 51, "price": 110, "stock": 18},
    {"name": "Bottled Water", "category": "Drinks", "barcode": "POS-DRINK-003", "cost": 18, "price": 35, "stock": 42},
    {"name": "Ham & Cheese Croissant", "category": "Pastry", "barcode": "POS-PASTRY-001", "cost": 46, "price": 95, "stock": 7},
    {"name": "Chocolate Cookie", "category": "Pastry", "barcode": "POS-PASTRY-002", "cost": 25, "price": 65, "stock": 5},
    {"name": "Chicken Pesto Pasta", "category": "Meals", "barcode": "POS-MEAL-001", "cost": 92, "price": 185, "stock": 14},
]


class Command(BaseCommand):
    help = "Create the OXPOS sample store and product catalog. Safe to run more than once."

    def add_arguments(self, parser):
        parser.add_argument("--store-id", default="P01", help="Store ID that receives the demo catalog.")

    def handle(self, *args, **options):
        store, _ = StoreSettings.objects.get_or_create(
            store_id=options["store_id"].strip().upper(),
            defaults={"business_name": "Sunrise Store"},
        )
        created = 0
        for item in PRODUCTS:
            _, was_created = Product.objects.update_or_create(
                store=store,
                barcode=item["barcode"],
                defaults=item,
            )
            created += int(was_created)
        self.stdout.write(self.style.SUCCESS(f"OXPOS demo ready. {created} new products created."))
