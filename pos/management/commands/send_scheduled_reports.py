from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.db.models import F, Q, Sum
from django.utils import timezone

from pos.models import Product, ReportSchedule, Sale


class Command(BaseCommand):
    help = "Send due Pro daily and weekly store-summary emails."

    def handle(self, *args, **options):
        now = timezone.now()
        today = timezone.localdate()
        sent = 0
        schedules = ReportSchedule.objects.filter(
            active=True,
            store__active_plan="Pro",
            store__status="Active",
        ).filter(
            Q(store__subscription_end__isnull=True)
            | Q(store__subscription_end__gte=today)
        ).select_related("store")
        for schedule in schedules:
            interval_days = 1 if schedule.frequency == ReportSchedule.Frequency.DAILY else 7
            if schedule.last_sent_at and schedule.last_sent_at.date() > today - timedelta(days=interval_days):
                continue
            start = today - timedelta(days=interval_days - 1)
            sales = Sale.objects.filter(
                store=schedule.store,
                status="Completed",
                created_at__date__gte=start,
                created_at__date__lte=today,
            )
            revenue = sales.aggregate(value=Sum("total"))["value"] or 0
            low_stock = Product.objects.filter(
                store=schedule.store,
                active=True,
                stock__lte=F("low_stock_threshold"),
            ).count()
            body = (
                f"{schedule.store.business_name} — {schedule.frequency} summary\n\n"
                f"Period: {start:%b %d, %Y} to {today:%b %d, %Y}\n"
                f"Completed orders: {sales.count()}\n"
                f"Revenue: PHP {revenue:.2f}\n"
                f"Low-stock products: {low_stock}\n\n"
                "Sign in to OXPOS for full reports and operational details."
            )
            try:
                send_mail(
                    f"{schedule.store.business_name} {schedule.frequency.lower()} POS summary",
                    body,
                    settings.DEFAULT_FROM_EMAIL,
                    [schedule.recipient_email],
                    fail_silently=False,
                )
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"Could not send to {schedule.recipient_email}: {exc}"))
                continue
            schedule.last_sent_at = now
            schedule.save(update_fields=["last_sent_at"])
            sent += 1
        self.stdout.write(self.style.SUCCESS(f"Sent {sent} scheduled report(s)."))
