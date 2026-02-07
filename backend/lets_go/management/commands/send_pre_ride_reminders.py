import time

from django.core.management.base import BaseCommand
from django.utils import timezone

from lets_go.models.models_trip import Trip
from lets_go.views_post_booking import (
    build_pre_ride_reminder_jobs_for_trip,
    fire_pre_ride_reminder_notifications,
)


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
                    self._run_once(window_minutes)
                    time.sleep(interval_seconds)
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING("Loop interrupted by user."))
        else:
            self._run_once(window_minutes)

    def _run_once(self, window_minutes: int):
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
            self.stdout.write(
                self.style.NOTICE(
                    f"[{now.isoformat()}] No trips eligible for pre-ride reminders."
                )
            )
            return

        self.stdout.write(
            f"[{now.isoformat()}] Checking {qs.count()} scheduled trips for pre-ride reminders"
        )

        sent_count = 0

        for trip in qs:
            try:
                jobs = build_pre_ride_reminder_jobs_for_trip(trip)
                driver_info = jobs.get("driver") or {}
                trigger_at_str = driver_info.get("trigger_at")
                if not trigger_at_str:
                    self.stdout.write(
                        f"[send_pre_ride_reminders] Trip {trip.trip_id}: missing driver trigger_at, skipping"
                    )
                    continue

                # Parse ISO string back to aware datetime
                trigger_at = timezone.datetime.fromisoformat(trigger_at_str)
                if timezone.is_naive(trigger_at):
                    trigger_at = timezone.make_aware(trigger_at)

                # Only send if now is after trigger time but within the allowed window
                if trigger_at <= now <= trigger_at + window_delta:
                    self.stdout.write(
                        f"Sending pre-ride reminder for trip {trip.trip_id} (driver_id={trip.driver_id})"
                    )
                    fire_pre_ride_reminder_notifications(trip)
                    trip.pre_ride_reminder_sent = True
                    trip.save(update_fields=["pre_ride_reminder_sent", "updated_at"])
                    sent_count += 1
                else:
                    self.stdout.write(
                        f"[send_pre_ride_reminders] Trip {trip.trip_id}: "
                        f"now={now.isoformat()} not in window "
                        f"[{trigger_at.isoformat()}, {(trigger_at + window_delta).isoformat()}], skipping"
                    )
            except Exception as e:
                self.stderr.write(
                    f"[send_pre_ride_reminders][ERROR] trip_id={trip.trip_id}: {e}"
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"[{now.isoformat()}] Pre-ride reminders sent for {sent_count} trip(s)."
            )
        )
