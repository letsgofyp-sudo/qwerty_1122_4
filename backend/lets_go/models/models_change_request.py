from django.db import models


class ChangeRequest(models.Model):
    ENTITY_USER_PROFILE = 'USER_PROFILE'
    ENTITY_VEHICLE = 'VEHICLE'
    ENTITY_CHOICES = [
        (ENTITY_USER_PROFILE, 'User Profile'),
        (ENTITY_VEHICLE, 'Vehicle'),
    ]

    STATUS_PENDING = 'PENDING'
    STATUS_APPROVED = 'APPROVED'
    STATUS_REJECTED = 'REJECTED'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_APPROVED, 'Approved'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    user = models.ForeignKey('UsersData', on_delete=models.CASCADE, related_name='change_requests')
    vehicle = models.ForeignKey('Vehicle', on_delete=models.CASCADE, null=True, blank=True, related_name='change_requests')

    entity_type = models.CharField(max_length=16, choices=ENTITY_CHOICES)

    original_data = models.JSONField(default=dict, blank=True)
    requested_changes = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_PENDING)
    review_notes = models.TextField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['entity_type', 'created_at']),
            models.Index(fields=['user', 'created_at']),
        ]
        ordering = ['-created_at']
