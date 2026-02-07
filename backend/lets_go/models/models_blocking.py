from django.db import models


class BlockedUser(models.Model):
    blocker = models.ForeignKey('UsersData', on_delete=models.CASCADE, related_name='blocked_users')
    blocked_user = models.ForeignKey('UsersData', on_delete=models.CASCADE, related_name='blocked_by_users')
    reason = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('blocker', 'blocked_user')
        indexes = [
            models.Index(fields=['blocker']),
            models.Index(fields=['blocked_user']),
        ]

    def __str__(self):
        return f"{self.blocker_id} blocked {self.blocked_user_id}"
