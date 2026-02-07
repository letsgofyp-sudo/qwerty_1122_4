import os

from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from lets_go.management.commands.send_pre_ride_reminders import run_pre_ride_reminders


@csrf_exempt
@require_http_methods(["POST"])
def run_pre_ride_reminders_view(request):
    """HTTP endpoint to trigger pre-ride reminder job.

    Protected by CRON_SECRET via the Authorization header.
    When using vercel.json "crons", Vercel sends
    Authorization: Bearer $CRON_SECRET
    """
    expected = os.environ.get("CRON_SECRET", "")
    auth_header = request.headers.get("Authorization", "")
    if not expected or auth_header != f"Bearer {expected}":
        return HttpResponseForbidden("Forbidden")

    window_minutes = 5
    count = run_pre_ride_reminders(window_minutes=window_minutes)
    return HttpResponse(f"OK: sent {count} pre-ride reminders")
