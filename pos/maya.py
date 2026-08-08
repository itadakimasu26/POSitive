import base64
import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction

from .models import SubscriptionRequest
from .subscriptions import activate_subscription


logger = logging.getLogger(__name__)


class MayaCheckoutError(Exception):
    pass


def _request_json(path, api_key, method="GET", payload=None):
    credentials = base64.b64encode(f"{api_key}:".encode()).decode()
    body = json.dumps(payload).encode() if payload is not None else None
    request = Request(
        f"{settings.MAYA_API_BASE_URL.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=settings.MAYA_API_TIMEOUT) as response:
            return json.loads(response.read().decode())
    except HTTPError as exc:
        logger.warning("Maya API returned HTTP %s for %s.", exc.code, path)
        raise MayaCheckoutError("Maya could not process the checkout request.") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        logger.warning("Maya API request failed for %s: %s", path, type(exc).__name__)
        raise MayaCheckoutError("Maya Checkout is temporarily unavailable. Please try again.") from exc


def create_checkout(subscription_request, success_url, failure_url, cancel_url):
    reference = subscription_request.provider_reference or uuid.uuid4().hex
    amount = subscription_request.amount.quantize(Decimal("0.01"))
    payload = {
        "totalAmount": {"value": float(amount), "currency": "PHP"},
        "buyer": {
            "firstName": subscription_request.requested_by.first_name or subscription_request.payer_name or "POSitive",
            "lastName": subscription_request.requested_by.last_name or "Customer",
        },
        "items": [
            {
                "name": f"POSitive {subscription_request.plan} subscription",
                "code": f"POSITIVE-{subscription_request.plan.upper()}",
                "description": "30-day POSitive point-of-sale subscription",
                "quantity": "1",
                "amount": {"value": float(amount)},
                "totalAmount": {"value": float(amount)},
            }
        ],
        "redirectUrl": {
            "success": success_url,
            "failure": failure_url,
            "cancel": cancel_url,
        },
        "requestReferenceNumber": reference,
    }
    result = _request_json(
        "/checkout/v1/checkouts",
        settings.MAYA_PUBLIC_API_KEY,
        method="POST",
        payload=payload,
    )
    checkout_id = result.get("checkoutId")
    redirect_url = result.get("redirectUrl")
    if not checkout_id or not redirect_url:
        raise MayaCheckoutError("Maya returned an incomplete checkout response.")
    subscription_request.provider_reference = reference
    subscription_request.provider_payment_id = checkout_id
    subscription_request.provider_checkout_url = redirect_url
    subscription_request.status = SubscriptionRequest.Status.AWAITING_PAYMENT
    subscription_request.save(
        update_fields=[
            "provider_reference",
            "provider_payment_id",
            "provider_checkout_url",
            "status",
        ]
    )
    return redirect_url


def retrieve_payment(payment_id):
    result = _request_json(
        f"/payments/v1/payments/{payment_id}",
        settings.MAYA_SECRET_API_KEY,
    )
    if isinstance(result, list):
        return next((item for item in result if item.get("id") == payment_id), result[0] if result else {})
    return result


def _payment_amount(payload):
    candidates = [payload.get("totalAmount"), payload.get("amount")]
    payment_total = (
        payload.get("paymentDetails", {})
        .get("responses", {})
        .get("efs", {})
        .get("amount", {})
        .get("total")
    )
    candidates.append(payment_total)
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get("value") is not None:
            try:
                return Decimal(str(candidate["value"])).quantize(Decimal("0.01"))
            except InvalidOperation:
                return None
    try:
        return sum(
            (
                Decimal(str(item["totalAmount"]["value"]))
                for item in payload.get("items", [])
            ),
            Decimal("0.00"),
        ).quantize(Decimal("0.01"))
    except (InvalidOperation, KeyError, TypeError, AttributeError):
        return None


@transaction.atomic
def reconcile_payment(subscription_request_id, payment_id):
    subscription_request = (
        SubscriptionRequest.objects.select_for_update()
        .select_related("store", "requested_by")
        .get(pk=subscription_request_id)
    )
    if subscription_request.activated_at:
        return subscription_request, False

    verified = retrieve_payment(payment_id)
    verified_reference = verified.get("requestReferenceNumber")
    verified_payment_id = verified.get("id")
    verified_status = verified.get("paymentStatus")
    verified_amount = _payment_amount(verified)
    if (
        verified_payment_id != payment_id
        or verified_reference != subscription_request.provider_reference
        or verified_amount != subscription_request.amount
    ):
        logger.warning("Rejected a Maya payment verification mismatch for subscription request %s.", subscription_request.pk)
        raise MayaCheckoutError("The Maya payment details could not be verified.")

    subscription_request.provider_payment_id = verified_payment_id
    if verified_status == "PAYMENT_SUCCESS":
        receipt = verified.get("receiptNumber") or verified_payment_id
        subscription_request.payment_reference = receipt
        subscription_request.save(update_fields=["provider_payment_id", "payment_reference"])
        return activate_subscription(
            subscription_request.pk,
            payment_reference=receipt,
        )

    statuses = {
        "PAYMENT_FAILED": SubscriptionRequest.Status.FAILED,
        "PAYMENT_EXPIRED": SubscriptionRequest.Status.FAILED,
        "PAYMENT_CANCELLED": SubscriptionRequest.Status.CANCELLED,
    }
    if verified_status in statuses:
        subscription_request.status = statuses[verified_status]
        subscription_request.save(update_fields=["provider_payment_id", "status"])
    return subscription_request, False
