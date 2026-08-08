from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import StoreAuditEvent, SubscriptionRequest


SUBSCRIPTION_DAYS = 30


@transaction.atomic
def activate_subscription(subscription_request_id, reviewed_by=None, payment_reference=""):
    subscription_request = (
        SubscriptionRequest.objects.select_for_update()
        .select_related("store", "requested_by")
        .get(pk=subscription_request_id)
    )
    if subscription_request.activated_at or subscription_request.status not in {
        SubscriptionRequest.Status.PENDING,
        SubscriptionRequest.Status.AWAITING_PAYMENT,
    }:
        return subscription_request, False

    store = subscription_request.store
    today = timezone.localdate()
    if store.active_plan == subscription_request.plan and store.subscription_end and store.subscription_end >= today:
        subscription_end = store.subscription_end + timedelta(days=SUBSCRIPTION_DAYS)
        subscription_start = store.subscription_start or today
    else:
        subscription_start = today
        subscription_end = today + timedelta(days=SUBSCRIPTION_DAYS - 1)

    store.active_plan = subscription_request.plan
    store.status = "Active"
    store.subscription_start = subscription_start
    store.subscription_end = subscription_end
    store.save(update_fields=["active_plan", "status", "subscription_start", "subscription_end", "updated_at"])

    now = timezone.now()
    subscription_request.status = SubscriptionRequest.Status.APPROVED
    subscription_request.reviewed_by = reviewed_by
    subscription_request.reviewed_at = now
    subscription_request.activated_at = now
    if payment_reference:
        subscription_request.payment_reference = payment_reference
    subscription_request.save(
        update_fields=[
            "status",
            "reviewed_by",
            "reviewed_at",
            "activated_at",
            "payment_reference",
        ]
    )
    StoreAuditEvent.objects.create(
        store=store,
        actor=subscription_request.requested_by,
        approved_by=reviewed_by,
        action="subscription.activated",
        description=(
            f"Activated {subscription_request.plan} through {subscription_request.get_payment_method_display()} "
            f"until {subscription_end.isoformat()}."
        ),
    )
    return subscription_request, True


@transaction.atomic
def reject_subscription(subscription_request_id, reviewed_by=None):
    subscription_request = SubscriptionRequest.objects.select_for_update().get(pk=subscription_request_id)
    if subscription_request.activated_at or subscription_request.status not in {
        SubscriptionRequest.Status.PENDING,
        SubscriptionRequest.Status.AWAITING_PAYMENT,
    }:
        return subscription_request, False
    subscription_request.status = SubscriptionRequest.Status.REJECTED
    subscription_request.reviewed_by = reviewed_by
    subscription_request.reviewed_at = timezone.now()
    subscription_request.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    return subscription_request, True
