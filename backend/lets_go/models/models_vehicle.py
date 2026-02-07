from django.db import models
from django.core.validators import RegexValidator, MinValueValidator
from django.core.exceptions import ValidationError

# Validator for Pakistani license plate (e.g., ABC-1234 or ABC-12-D)
plate_validator = RegexValidator(
    r'^[A-Z]{2,3}-\d{1,4}(-[A-Z])?$',
    'Enter a valid Pakistani vehicle plate, e.g. "ABC-1234" or "AB-123".'
)

class Vehicle(models.Model):
    TWO_WHEELER = 'TW'
    FOUR_WHEELER = 'FW'
    TYPE_CHOICES = [
        (TWO_WHEELER, 'Two Wheeler'),
        (FOUR_WHEELER, 'Four Wheeler'),
    ]

    STATUS_PENDING = 'PENDING'
    STATUS_VERIFIED = 'VERIFIED'
    STATUS_REJECTED = 'REJECTED'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_VERIFIED, 'Verified'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    id = models.AutoField(primary_key=True)
    owner = models.ForeignKey(
        'UsersData', on_delete=models.CASCADE,
        related_name='vehicles', verbose_name='User'
    )
    model_number = models.CharField(max_length=50)
    variant = models.CharField(max_length=50, blank=True)
    company_name = models.CharField(max_length=50)
    plate_number = models.CharField(
        max_length=10,
        unique=True,
        validators=[plate_validator],
        verbose_name='Registration Number'
    )
    vehicle_type = models.CharField(
        max_length=2,
        choices=TYPE_CHOICES,
        default=TWO_WHEELER,
        verbose_name='Type'
    )
    color = models.CharField(max_length=30, blank=True)
    photo_front_url = models.URLField(null=True, blank=True)
    photo_back_url = models.URLField(null=True, blank=True)
    documents_image_url = models.URLField(null=True, blank=True)
    seats = models.PositiveIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1)],
        verbose_name='Number of Seats'
    )
    engine_number = models.CharField(max_length=50, blank=True)
    chassis_number = models.CharField(max_length=50, blank=True)
    fuel_type = models.CharField(
        max_length=10,
        choices=[
            ('Petrol', 'Petrol'),
            ('Diesel', 'Diesel'),
            ('CNG', 'CNG'),
            ('Electric', 'Electric'),
            ('Hybrid', 'Hybrid'),
        ],
        blank=True
    )
    registration_date = models.DateField(null=True, blank=True)
    insurance_expiry = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=10,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        # Ensure seats only for four wheelers
        if self.vehicle_type == self.FOUR_WHEELER:
            if self.seats is None:
                raise ValidationError({'seats': 'Please specify number of seats for four wheelers.'})
        else:
            # Two wheelers should not set seats
            if self.seats is not None:
                raise ValidationError({'seats': 'Seats field is only for four wheelers.'})

    def __str__(self):
        return f"{self.plate_number} ({self.get_vehicle_type_display()})"
