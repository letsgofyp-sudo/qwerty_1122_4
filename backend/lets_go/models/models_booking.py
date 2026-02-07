from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.contrib.auth.hashers import make_password, check_password

class Booking(models.Model):
    """Model for passenger bookings with multiple seats"""
    BOOKING_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('CONFIRMED', 'Confirmed'),
        ('CANCELLED', 'Cancelled'),
        ('COMPLETED', 'Completed'),
    ]

    RIDE_STATUS_CHOICES = [
        ('NOT_STARTED', 'Not Started'),
        ('RIDE_STARTED', 'Ride Started'),
        ('DROPPED_OFF', 'Dropped Off'),
        ('DROPPED_EARLY', 'Dropped Early'),
        ('CANCELLED_ON_BOARD', 'Cancelled On Board'),
    ]
    
    PAYMENT_STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('COMPLETED', 'Completed'),
        ('FAILED', 'Failed'),
        ('REFUNDED', 'Refunded'),
    ]
    
    booking_id = models.CharField(
        max_length=50, 
        unique=True, 
        help_text="Unique booking identifier like B001-2024-01-15-08:00-001"
    )
    trip = models.ForeignKey('Trip', on_delete=models.CASCADE, related_name='trip_bookings')
    passenger = models.ForeignKey('UsersData', on_delete=models.CASCADE, related_name='passenger_bookings')
    
    # Route details
    from_stop = models.ForeignKey(
        'RouteStop', 
        on_delete=models.CASCADE, 
        related_name='bookings_from',
        help_text="Pickup stop"
    )
    to_stop = models.ForeignKey(
        'RouteStop', 
        on_delete=models.CASCADE, 
        related_name='bookings_to',
        help_text="Drop-off stop"
    )
    
    # Seat details
    number_of_seats = models.IntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Number of seats booked"
    )

    male_seats = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Number of male seats in this booking"
    )
    female_seats = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)],
        help_text="Number of female seats in this booking"
    )
    seats_locked = models.BooleanField(
        default=False,
        help_text="Whether seats are currently reserved/locked for this booking request"
    )
    seat_numbers = models.JSONField(
        default=list,
        help_text="Array of seat numbers booked"
    )
    
    # Fare details
    total_fare = models.IntegerField(
        validators=[MinValueValidator(1)],
        help_text="Total fare for all seats"
    )
    fare_breakdown = models.JSONField(
        default=dict,
        blank=True,
        help_text="Detailed breakdown of fare calculation"
    )
    
    # Bargaining and negotiation fields
    original_fare = models.IntegerField(
        null=True,
        blank=True,
        help_text="Original fare before negotiation"
    )
    negotiated_fare = models.IntegerField(
        null=True,
        blank=True,
        help_text="Final agreed fare after negotiation"
    )
    bargaining_status = models.CharField(
        max_length=20,
        choices=[
            ('NO_NEGOTIATION', 'No Negotiation'),
            ('PENDING', 'Pending Driver Response'),
            ('ACCEPTED', 'Accepted by Driver'),
            ('REJECTED', 'Rejected by Driver'),
            ('COUNTER_OFFER', 'Driver Counter Offer'),
            ('BLOCKED', 'Blocked'),
        ],
        default='NO_NEGOTIATION',
        help_text="Current status of price negotiation"
    )
    passenger_offer = models.IntegerField(
        null=True,
        blank=True,
        help_text="Passenger's proposed fare"
    )
    driver_response = models.TextField(
        null=True,
        blank=True,
        help_text="Driver's response to passenger's offer"
    )
    negotiation_notes = models.TextField(
        null=True,
        blank=True,
        help_text="Additional notes about the negotiation"
    )

    # Driver blocked passenger for this trip
    blocked = models.BooleanField(
        default=False,
        help_text='If true, passenger is blocked from requesting this trip again'
    )
    
    # Status
    booking_status = models.CharField(
        max_length=20, 
        choices=BOOKING_STATUS_CHOICES, 
        default='PENDING'
    )
    payment_status = models.CharField(
        max_length=20, 
        choices=PAYMENT_STATUS_CHOICES, 
        default='PENDING'
    )
    
    # Passenger feedback
    passenger_rating = models.DecimalField(
        max_digits=3, 
        decimal_places=2,
        validators=[MinValueValidator(1.0), MaxValueValidator(5.0)],
        null=True, 
        blank=True,
        help_text="Passenger rating (1.0 to 5.0)"
    )
    passenger_feedback = models.TextField(
        null=True, 
        blank=True,
        help_text="Passenger feedback about the trip"
    )

    driver_rating = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        validators=[MinValueValidator(1.0), MaxValueValidator(5.0)],
        null=True,
        blank=True,
    )
    driver_feedback = models.TextField(
        null=True,
        blank=True,
    )
    
    # Timestamps
    booked_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    started_by_passenger = models.ForeignKey(
        'UsersData',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='started_bookings'
    )

    readiness_status = models.CharField(
        max_length=16,
        choices=[
            ('UNKNOWN', 'Unknown'),
            ('READY', 'Ready'),
            ('NOT_READY', 'Not Ready'),
        ],
        default='UNKNOWN'
    )

    pre_ride_reminder_sent = models.BooleanField(
        default=False,
        help_text="Has the T-10 pre-ride reminder been sent for this booking?",
    )

    ride_status = models.CharField(
        max_length=20,
        choices=RIDE_STATUS_CHOICES,
        default='NOT_STARTED'
    )
    pickup_verified_at = models.DateTimeField(null=True, blank=True)
    dropoff_at = models.DateTimeField(null=True, blank=True)
    dropped_early_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['trip']),
            models.Index(fields=['passenger']),
            models.Index(fields=['booking_status']),
            models.Index(fields=['payment_status']),
            models.Index(fields=['booked_at']),
            models.Index(fields=['seats_locked']),
        ]
        ordering = ['-booked_at']

    def __str__(self):
        return f"Booking {self.booking_id}: {self.passenger.name} - {self.number_of_seats} seats"
    
    @property
    def is_active(self):
        """Check if booking is active (confirmed)"""
        return self.booking_status == 'CONFIRMED'
    
    @property
    def can_cancel(self):
        """Check if booking can be cancelled"""
        if self.booking_status not in ['PENDING', 'CONFIRMED']:
            return False
        # Before trip starts: cancel is always allowed.
        if self.trip.trip_status == 'SCHEDULED':
            return True
        # During trip: allow cancel only if passenger is not on-board yet.
        if self.trip.trip_status == 'IN_PROGRESS':
            return (self.ride_status or 'NOT_STARTED') == 'NOT_STARTED'
        return False
    
    def clean(self):
        """Validate booking data"""
        if self.number_of_seats <= 0:
            raise ValidationError({'number_of_seats': 'Number of seats must be greater than 0.'})

        # If split counts are provided, ensure they are consistent.
        split_total = int(self.male_seats or 0) + int(self.female_seats or 0)
        if split_total > 0 and split_total != int(self.number_of_seats or 0):
            raise ValidationError({'number_of_seats': 'number_of_seats must equal male_seats + female_seats.'})
        
        if self.total_fare <= 0:
            raise ValidationError({'total_fare': 'Total fare must be greater than 0.'})
        
        # Check if stops belong to the same route as trip
        if self.from_stop.route != self.trip.route or self.to_stop.route != self.trip.route:
            raise ValidationError('Both stops must belong to the same route as the trip.')
        
        # Check if pickup stop comes before drop-off stop
        if self.from_stop.stop_order >= self.to_stop.stop_order:
            raise ValidationError('Pickup stop must come before drop-off stop.')
        
        # Check if enough seats are available
        if self.trip.available_seats < self.number_of_seats:
            raise ValidationError(f'Only {self.trip.available_seats} seats available, but {self.number_of_seats} requested.')
    
    def save(self, *args, **kwargs):
        """Override save to update trip's available seats"""
        # Keep totals consistent. New clients send male_seats/female_seats.
        split_total = int(self.male_seats or 0) + int(self.female_seats or 0)
        if split_total > 0:
            self.number_of_seats = split_total
        else:
            # Legacy clients may only set number_of_seats.
            # Backfill split counts based on passenger gender (best-effort).
            try:
                g = (getattr(self.passenger, 'gender', None) or '').lower()
            except Exception:
                g = ''
            if int(self.number_of_seats or 0) > 0:
                if g == 'female':
                    self.female_seats = int(self.number_of_seats)
                    self.male_seats = 0
                else:
                    self.male_seats = int(self.number_of_seats)
                    self.female_seats = 0

        if self.pk is None:  # New booking
            # Only deduct seats if booking is confirmed, not for pending requests
            if self.booking_status == 'CONFIRMED':
                self.trip.available_seats -= self.number_of_seats
                self.trip.save()
                
                # Add passenger to chat group
                try:
                    self.trip.chat_group.add_member(self.passenger, 'PASSENGER')
                    self.trip.chat_group.send_system_message(f"👋 {self.passenger.name} joined the trip!")
                except:
                    pass  # Chat group might not exist yet
        
        super().save(*args, **kwargs)
    
    def cancel_booking(self, reason=None):
        """Cancel the booking"""
        if not self.can_cancel:
            raise ValidationError('This booking cannot be cancelled.')

        was_confirmed = self.booking_status == 'CONFIRMED'
        self.booking_status = 'CANCELLED'
        self.cancelled_at = timezone.now()
        self.save(update_fields=['booking_status', 'cancelled_at', 'updated_at'])

        if self.seats_locked:
            self.trip.available_seats += self.number_of_seats
            self.trip.save(update_fields=['available_seats'])
            self.seats_locked = False
            self.save(update_fields=['seats_locked', 'updated_at'])
        elif was_confirmed:
            self.trip.available_seats += self.number_of_seats
            self.trip.save(update_fields=['available_seats'])

        if was_confirmed:
            try:
                self.trip.chat_group.remove_member(self.passenger)
                self.trip.chat_group.send_system_message(f"❌ {self.passenger.name} cancelled their booking")
            except:
                pass
    
    def complete_booking(self):
        """Mark booking as completed"""
        if self.booking_status != 'CONFIRMED':
            raise ValidationError('Only confirmed bookings can be completed.')
        
        self.booking_status = 'COMPLETED'
        self.completed_at = timezone.now()
        self.save()
    
    def update_payment_status(self, status):
        """Update payment status"""
        if status not in dict(self.PAYMENT_STATUS_CHOICES):
            raise ValidationError('Invalid payment status.')
        
        self.payment_status = status
        self.save()

class PickupCodeVerification(models.Model):
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('VERIFIED', 'Verified'),
        ('FAILED', 'Failed'),
        ('EXPIRED', 'Expired'),
    ]

    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name='pickup_codes')
    trip = models.ForeignKey('Trip', on_delete=models.CASCADE, related_name='pickup_codes')
    driver = models.ForeignKey('UsersData', on_delete=models.CASCADE, related_name='generated_pickup_codes')
    passenger = models.ForeignKey('UsersData', on_delete=models.CASCADE, related_name='pickup_code_verifications')

    code_hash = models.CharField(max_length=128)
    generated_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    max_attempts = models.IntegerField(default=3, validators=[MinValueValidator(1)])
    attempts = models.IntegerField(default=0, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='ACTIVE')

    driver_latitude = models.DecimalField(max_digits=10, decimal_places=8, null=True, blank=True)
    driver_longitude = models.DecimalField(max_digits=11, decimal_places=8, null=True, blank=True)
    passenger_latitude = models.DecimalField(max_digits=10, decimal_places=8, null=True, blank=True)
    passenger_longitude = models.DecimalField(max_digits=11, decimal_places=8, null=True, blank=True)

    last_attempt_at = models.DateTimeField(null=True, blank=True)
    verification_result = models.CharField(max_length=32, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['booking']),
            models.Index(fields=['trip']),
            models.Index(fields=['driver']),
            models.Index(fields=['passenger']),
            models.Index(fields=['status']),
        ]

    def set_code(self, raw_code: str):
        self.code_hash = make_password(raw_code)

    def check_code(self, raw_code: str) -> bool:
        return check_password(raw_code, self.code_hash)