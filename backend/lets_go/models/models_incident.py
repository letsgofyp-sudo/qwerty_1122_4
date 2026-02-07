from django.conf import settings
from django.db import models
from django.utils import timezone
import secrets


class SosIncident(models.Model):
    STATUS_OPEN = 'OPEN'
    STATUS_RESOLVED = 'RESOLVED'

    STATUS_CHOICES = [
        (STATUS_OPEN, 'Open'),
        (STATUS_RESOLVED, 'Resolved'),
    ]

    trip = models.ForeignKey('Trip', on_delete=models.CASCADE, related_name='sos_incidents')
    booking = models.ForeignKey('Booking', on_delete=models.SET_NULL, null=True, blank=True, related_name='sos_incidents')
    actor = models.ForeignKey('UsersData', on_delete=models.SET_NULL, null=True, blank=True, related_name='sos_incidents')
    audit_event = models.ForeignKey('RideAuditEvent', on_delete=models.SET_NULL, null=True, blank=True, related_name='sos_incidents')

    role = models.CharField(max_length=16)
    latitude = models.DecimalField(max_digits=10, decimal_places=8)
    longitude = models.DecimalField(max_digits=11, decimal_places=8)
    accuracy = models.FloatField(null=True, blank=True)
    note = models.TextField(null=True, blank=True)

    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_OPEN)
    created_at = models.DateTimeField(default=timezone.now)

    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='resolved_sos_incidents')
    resolved_note = models.TextField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['trip', 'created_at']),
            models.Index(fields=['actor', 'created_at']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        trip_id = getattr(self.trip, 'trip_id', None) or str(self.trip_id)
        return f"SOS {self.id} ({self.status}) trip={trip_id}"


class SosShareToken(models.Model):
    incident = models.ForeignKey('SosIncident', on_delete=models.CASCADE, related_name='share_tokens')
    token = models.CharField(max_length=96, unique=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['expires_at']),
            models.Index(fields=['revoked_at']),
        ]
        ordering = ['-created_at']

    def is_active(self):
        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return True
        return timezone.now() < self.expires_at

    @classmethod
    def mint(cls, incident, expires_at=None):
        return cls.objects.create(
            incident=incident,
            token=secrets.token_urlsafe(48),
            expires_at=expires_at,
            created_at=timezone.now(),
        )


class TripShareToken(models.Model):
    trip = models.ForeignKey('Trip', on_delete=models.CASCADE, related_name='share_tokens')
    booking = models.ForeignKey('Booking', on_delete=models.SET_NULL, null=True, blank=True, related_name='share_tokens')
    role = models.CharField(max_length=16)

    token = models.CharField(max_length=96, unique=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['expires_at']),
            models.Index(fields=['revoked_at']),
        ]
        ordering = ['-created_at']

    def is_active(self):
        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return True
        return timezone.now() < self.expires_at

    @classmethod
    def mint(cls, trip, role, booking=None, expires_at=None):
        return cls.objects.create(
            trip=trip,
            booking=booking,
            role=role,
            token=secrets.token_urlsafe(48),
            expires_at=expires_at,
            created_at=timezone.now(),
        )
