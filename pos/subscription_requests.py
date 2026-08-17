import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone


logger = logging.getLogger(__name__)


def deliver_extension_status_email(extension_request):
    """Notify the requester of the latest review status without exposing admin notes."""
    requester = extension_request.requested_by
    if not requester or not requester.email:
        extension_request.status_email_error = "The requester has no email address."
        extension_request.save(update_fields=["status_email_error", "updated_at"])
        return False

    email_context = {
        "extension_request": extension_request,
        "requester_name": requester.get_full_name() or requester.username,
        "support_email": settings.SUPPORT_CONTACT_EMAIL,
    }
    body = render_to_string("email/subscription_extension_status.txt", email_context)
    html_body = render_to_string("email/subscription_extension_status.html", email_context)

    try:
        message = EmailMultiAlternatives(
            subject=(
                f"OXPOS extension request {extension_request.status.lower()} - "
                f"{extension_request.store.store_id}"
            ),
            body=body,
            to=[requester.email],
        )
        message.attach_alternative(html_body, "text/html")
        delivered = message.send(fail_silently=False)
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
