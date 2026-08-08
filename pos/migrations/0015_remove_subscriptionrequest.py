from django.db import migrations


class Migration(migrations.Migration):
    """Remove the retired subscription-payment request workflow.

    Plan names, prices, subscription dates, and access controls continue to
    live on StoreSettings. Only the payment-provider/request data is removed.
    """

    dependencies = [
        ("pos", "0014_limit_dining_to_cafes"),
    ]

    operations = [
        migrations.DeleteModel(name="SubscriptionRequest"),
    ]
