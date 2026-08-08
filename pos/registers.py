from django.db import transaction
from django.utils import timezone

from .models import CashRegisterActivity, CashRegisterSession


@transaction.atomic
def assign_open_register(store, user, notes=""):
    register = CashRegisterSession.objects.select_for_update().filter(store=store, closed_at__isnull=True).first()
    if register is None:
        return None, None

    active = register.activities.select_for_update().filter(ended_at__isnull=True).first()
    if active and active.user_id == user.pk:
        return register, active

    reason = CashRegisterActivity.StartReason.RESUMED
    if active:
        active.ended_at = timezone.now()
        active.end_reason = CashRegisterActivity.EndReason.HANDOVER
        active.save(update_fields=["ended_at", "end_reason"])
        reason = CashRegisterActivity.StartReason.HANDOVER

    activity = CashRegisterActivity.objects.create(
        register=register,
        user=user,
        start_reason=reason,
        notes=notes.strip(),
    )
    return register, activity


@transaction.atomic
def release_user_registers(user, end_reason=CashRegisterActivity.EndReason.LOGOUT):
    activities = list(
        CashRegisterActivity.objects.select_for_update()
        .filter(user=user, ended_at__isnull=True, register__closed_at__isnull=True)
    )
    now = timezone.now()
    for activity in activities:
        activity.ended_at = now
        activity.end_reason = end_reason
        activity.save(update_fields=["ended_at", "end_reason"])
    return len(activities)
