import logging

from django.core.mail import EmailMessage
from django.utils import timezone


logger = logging.getLogger(__name__)


def deliver_extension_status_email(extension_request):
    """Notify the requester of the latest review status without exposing admin notes."""
    requester = extension_request.requested_by
    if not requester or not requester.email:
        extension_request.status_email_error = "The requester has no email address."
        extension_request.save(update_fields=["status_email_error", "updated_at"])
        return False

    body_lines = [
        f"Hello {requester.get_full_name() or requester.username},",
        "",
        f"Your subscription extension request for {extension_request.store.business_name} "
        f"({extension_request.store.store_id}) is now {extension_request.status}.",
        "",
        f"Requested plan: {extension_request.requested_plan}",
        f"Preferred payment type: {extension_request.payment_type}",
    ]
    if extension_request.activated_at:
        body_lines.extend(
            [
                f"Subscription term: {extension_request.extension_start:%b %d, %Y} "
                f"through {extension_request.extension_end:%b %d, %Y}",
                "Your store access is active now.",
            ]
        )
    elif extension_request.status == extension_request.Status.IN_REVIEW:
        body_lines.append("The OXPOS administration team is reviewing your request.")
    elif extension_request.status == extension_request.Status.DECLINED:
        body_lines.append("Please contact OXPOS if you would like help with another plan or payment option.")
    body_lines.extend(["", "Thank you,", "OXPOS Administration"])

    try:
        delivered = EmailMessage(
            subject=(
                f"OXPOS extension request {extension_request.status.lower()} - "
                f"{extension_request.store.store_id}"
            ),
            body="\n".join(body_lines),
            to=[requester.email],
        ).send(fail_silently=False)
        if delivered != 1:
            raise RuntimeError("The configured email backend did not accept the message.")
    except Exception as exc:
        logger.exception(
            "Subscription extension status email failed for request %s.",
            extension_request.pk,
        )
        extension_request.status_email_error = f"{type(exc).__name__}: {exc}"[:500]
        extension_request.save(update_fields=["status_email_error", "updated_at"])
        return False

    extension_request.status_email_sent_at = timezone.now()
    extension_request.status_email_error = ""
    extension_request.save(
        update_fields=["status_email_sent_at", "status_email_error", "updated_at"]
    )
    return True
