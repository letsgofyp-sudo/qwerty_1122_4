import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from lets_go.models.models_trip import Trip
from lets_go.views_post_booking import (
    build_pre_ride_reminder_jobs_for_trip,
    fire_pre_ride_reminder_notifications,
)


def run_pre_ride_reminders(window_minutes: int = 5) -> int:
    """Run a single pass of the pre-ride reminder job.

    This function is safe to call from short-lived contexts such as
    HTTP views or serverless functions (e.g. Vercel cron triggers).
    It returns the number of reminders sent.
    """
    now = timezone.now()
    window_delta = timezone.timedelta(minutes=window_minutes)

    # Limit to trips near today to avoid scanning the whole table
    today = now.date()

    qs = Trip.objects.filter(
        trip_status="SCHEDULED",
        pre_ride_reminder_sent=False,
        trip_date__gte=today - timezone.timedelta(days=1),
        trip_date__lte=today + timezone.timedelta(days=1),
    )

    if not qs.exists():
        return 0

    sent_count = 0

    for trip in qs:
        try:
            jobs = build_pre_ride_reminder_jobs_for_trip(trip)
            driver_info = jobs.get("driver") or {}
            trigger_at_str = driver_info.get("trigger_at")
            if not trigger_at_str:
                continue

            # Parse ISO string back to aware datetime
            trigger_at = timezone.datetime.fromisoformat(trigger_at_str)
            if timezone.is_naive(trigger_at):
                trigger_at = timezone.make_aware(trigger_at)

            # Only send if now is after trigger time but within the allowed window
            if trigger_at <= now <= trigger_at + window_delta:
                fire_pre_ride_reminder_notifications(trip)
                trip.pre_ride_reminder_sent = True
                trip.save(update_fields=["pre_ride_reminder_sent", "updated_at"])
                sent_count += 1
        except Exception:
            # Swallow per-trip errors so one bad trip does not abort the whole run
            continue

    return sent_count


class Command(BaseCommand):
    help = "Send T-10 pre-ride reminder notifications for upcoming trips"

    def add_arguments(self, parser):
        parser.add_argument(
            "--window-minutes",
            type=int,
            default=5,
            help="How many minutes after the trigger time reminders are allowed to be sent.",
        )
        parser.add_argument(
            "--loop",
            action="store_true",
            help="If set, run in a continuous loop instead of a single run.",
        )
        parser.add_argument(
            "--interval-seconds",
            type=int,
            default=60,
            help="Sleep interval between runs when --loop is enabled.",
        )

    def handle(self, *args, **options):
        window_minutes = options["window_minutes"]
        loop = options["loop"]
        interval_seconds = options["interval_seconds"]

        if loop:
            self.stdout.write(
                self.style.NOTICE(
                    f"Starting send_pre_ride_reminders in loop mode: "
                    f"window={window_minutes}min, interval={interval_seconds}s"
                )
            )
            try:
                while True:
                    count = run_pre_ride_reminders(window_minutes)
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"Pre-ride reminders sent for {count} trip(s) in this iteration."
                        )
                    )
                    time.sleep(interval_seconds)
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING("Loop interrupted by user."))
        else:
            count = run_pre_ride_reminders(window_minutes)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Pre-ride reminders sent for {count} trip(s)."
                )
            )
