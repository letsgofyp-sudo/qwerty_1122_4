from django.db import models
from django.core.validators import RegexValidator

from .models_userdata import UsersData


class EmergencyContact(models.Model):
    user = models.OneToOneField(
        UsersData,
        on_delete=models.CASCADE,
        related_name='emergency_contact',
    )
    name = models.CharField(max_length=100)
    relation = models.CharField(max_length=50)
    email = models.EmailField()
    phone_no = models.CharField(
        max_length=16,
        validators=[
            RegexValidator(
                regex=r"^\d{10,15}$",
                message="Emergency phone must be 10-15 digits (no country prefix)",
            )
        ],
    )

    def __str__(self):
        return f"{self.name} ({self.relation}) for {self.user.name}"
