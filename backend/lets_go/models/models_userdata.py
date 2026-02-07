from django.db import models
from django.core.validators import RegexValidator, MinLengthValidator, ValidationError
from django.utils.translation import gettext_lazy as _


class UsernameRegistry(models.Model):
    """Dedicated table for tracking reserved usernames.

    This is used during signup to ensure a username is unique *before* a
    UsersData record exists. The UsersData.username field can still remain
    unique at the database level as the final source of truth.
    """
    username = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.username


class UsersData(models.Model):
    name = models.CharField(max_length=100)
    username = models.CharField(max_length=100, unique=True)
    email = models.EmailField(unique=True)
    password = models.CharField(
        max_length=128,
        validators=[
            MinLengthValidator(8),
        ]
    )
    address = models.TextField()
    phone_no = models.CharField(
        max_length=16,  # allow for + and up to 15 digits
        validators=[
            RegexValidator(
                regex=r"^\+\d{10,15}$",
                message="Phone number must be in international format, e.g. +923001234567"
            )
        ]
    )
    cnic_no = models.CharField(
        max_length=15,
        validators=[
            RegexValidator(
                regex=r"^\d{5}-\d{7}-\d{1}$",
                message="CNIC must be in the format 36603-0269853-9"
            )
        ]
    )
    gender = models.CharField(
        max_length=10,
        choices=[('male', 'Male'), ('female', 'Female')]
    )
    driving_license_no = models.CharField(max_length=15, null=True, blank=True)
    accountno = models.CharField(max_length=20, null=True, blank=True)
    bankname = models.CharField(max_length=50, null=True, blank=True)
    iban = models.CharField(max_length=34, null=True, blank=True)
    profile_photo_url = models.URLField(null=True, blank=True)
    live_photo_url = models.URLField(null=True, blank=True)
    cnic_front_image_url = models.URLField(null=True, blank=True)
    cnic_back_image_url = models.URLField(null=True, blank=True)
    status = models.CharField(
        max_length=10,
        default='PENDING',
        choices=[
            ('PENDING', 'Pending'),
            ('VERIFIED', 'Verified'),
            ('REJECTED', 'Rejected'),
            ('BANNED', 'Banned'),
        ]
    )
    rejection_reason = models.TextField(null=True, blank=True)
    driving_license_front_url = models.URLField(null=True, blank=True)
    driving_license_back_url = models.URLField(null=True, blank=True)
    accountqr_url = models.URLField(null=True, blank=True)
    driver_rating = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    passenger_rating = models.DecimalField(max_digits=3, decimal_places=2, null=True, blank=True)
    fcm_token = models.TextField(null=True, blank=True, help_text='Firebase Cloud Messaging token for push notifications')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        # Password complexity
        import re
        if self.password:
            if not re.search(r"[A-Z]", self.password):
                raise ValidationError({'password': _('Password must contain at least one uppercase letter.')})
            if not re.search(r"[a-z]", self.password):
                raise ValidationError({'password': _('Password must contain at least one lowercase letter.')})
            if not re.search(r"\d", self.password):
                raise ValidationError({'password': _('Password must contain at least one digit.')})
            if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]", self.password):
                raise ValidationError({'password': _('Password must contain at least one special character.')})

        # If driving_license_no is provided, both images are required
        if self.driving_license_no:
            if not self.driving_license_front_url or not self.driving_license_back_url:
                raise ValidationError(_('Both front and back images of the driving license are required if license number is provided.'))

    def __str__(self):
        return self.name
