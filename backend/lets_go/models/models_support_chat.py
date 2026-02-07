from django.db import models
from django.db.models import Q
from django.utils import timezone


class GuestUser(models.Model):
    guest_number = models.PositiveIntegerField(unique=True)
    username = models.CharField(max_length=100, unique=True)
    fcm_token = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['guest_number']),
            models.Index(fields=['username']),
        ]

    def __str__(self):
        return self.username


class SupportThread(models.Model):
    THREAD_TYPE_CHOICES = [
        ('BOT', 'Bot'),
        ('ADMIN', 'Admin'),
    ]

    user = models.ForeignKey(
        'UsersData',
        on_delete=models.CASCADE,
        related_name='support_threads',
        null=True,
        blank=True,
    )
    guest = models.ForeignKey(
        'GuestUser',
        on_delete=models.CASCADE,
        related_name='support_threads',
        null=True,
        blank=True,
    )
    thread_type = models.CharField(max_length=10, choices=THREAD_TYPE_CHOICES)
    is_closed = models.BooleanField(default=False)
    last_message_at = models.DateTimeField(null=True, blank=True)
    user_last_seen_id = models.BigIntegerField(default=0)
    admin_last_seen_id = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'thread_type']),
            models.Index(fields=['guest', 'thread_type']),
            models.Index(fields=['thread_type', 'last_message_at']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'thread_type'],
                condition=Q(user__isnull=False),
                name='uniq_supportthread_user_thread_type',
            ),
            models.UniqueConstraint(
                fields=['guest', 'thread_type'],
                condition=Q(guest__isnull=False),
                name='uniq_supportthread_guest_thread_type',
            ),
            models.CheckConstraint(
                check=(
                    Q(user__isnull=False, guest__isnull=True)
                    | Q(user__isnull=True, guest__isnull=False)
                ),
                name='chk_supportthread_owner_xor',
            ),
        ]

    def touch(self):
        self.last_message_at = timezone.now()
        self.save(update_fields=['last_message_at', 'updated_at'])


class SupportMessage(models.Model):
    SENDER_TYPE_CHOICES = [
        ('USER', 'User'),
        ('BOT', 'Bot'),
        ('ADMIN', 'Admin'),
    ]

    thread = models.ForeignKey(SupportThread, on_delete=models.CASCADE, related_name='messages')
    sender_type = models.CharField(max_length=10, choices=SENDER_TYPE_CHOICES)
    sender_user = models.ForeignKey(
        'UsersData',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='support_messages_sent',
    )
    message_text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['thread', 'created_at']),
        ]
        ordering = ['created_at']
