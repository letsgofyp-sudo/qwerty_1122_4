from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import timedelta

class Trip(models.Model):
    """Model for individual bus/shuttle trips"""
    TRIP_STATUS_CHOICES = [
        ('SCHEDULED', 'Scheduled'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    trip_id = models.CharField(
        max_length=50, 
        unique=True, 
        help_text="Unique trip identifier like T001-2024-01-15-08:00"
    )
    route = models.ForeignKey('Route', on_delete=models.CASCADE, related_name='trips')
    vehicle = models.ForeignKey('Vehicle', on_delete=models.SET_NULL, null=True, blank=True)
    driver = models.ForeignKey('UsersData', on_delete=models.CASCADE, related_name='driver_trips')
    
    # Trip timing
    trip_date = models.DateField(help_text="Date of the trip")
    departure_time = models.TimeField(help_text="Scheduled departure time")
    estimated_arrival_time = models.TimeField(help_text="Expected arrival time")
    actual_departure_time = models.TimeField(null=True, blank=True, help_text="Actual departure time")
    actual_arrival_time = models.TimeField(null=True, blank=True, help_text="Actual arrival time")
    
    # Trip status and capacity
    trip_status = models.CharField(
        max_length=20, 
        choices=TRIP_STATUS_CHOICES, 
        default='SCHEDULED'
    )
    total_seats = models.IntegerField(
        validators=[MinValueValidator(1)],
        help_text="Total number of seats available"
    )
    available_seats = models.IntegerField(
        validators=[MinValueValidator(0)],
        help_text="Number of seats still available"
    )
    base_fare = models.IntegerField(
        validators=[MinValueValidator(1)],
        help_text="Base fare for this trip"
    )
    
    started_by_user = models.ForeignKey(
        'UsersData',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='started_trips'
    )
    
    live_tracking_state = models.JSONField(
        default=dict,
        blank=True
    )
    
    # Enhanced fare calculation fields
    total_distance_km = models.DecimalField(
        max_digits=8, 
        decimal_places=2,
        null=True, 
        blank=True,
        help_text="Total calculated distance from frontend"
    )
    total_duration_minutes = models.IntegerField(
        null=True, 
        blank=True,
        help_text="Total calculated duration from frontend"
    )
    fare_calculation = models.JSONField(
        default=dict,
        blank=True,
        help_text="Complete frontend fare calculation breakdown"
    )
    
    # Trip details
    notes = models.TextField(null=True, blank=True, help_text="Additional notes about the trip")
    gender_preference = models.CharField(
        max_length=10,
        choices=[('Male', 'Male'), ('Female', 'Female'), ('Any', 'Any')],
        default='Any',
        help_text="Gender preference for this trip (Male, Female, Any)"
    )
    cancellation_reason = models.CharField(
        max_length=255, 
        null=True, 
        blank=True,
        help_text="Reason for cancellation if applicable"
    )
    
    # Bargaining and pricing fields
    is_negotiable = models.BooleanField(
        default=True,
        help_text="Whether the driver is open to price negotiation"
    )
    minimum_acceptable_fare = models.IntegerField(
        null=True,
        blank=True,
        help_text="Minimum fare driver is willing to accept"
    )
    bargaining_history = models.JSONField(
        default=list,
        blank=True,
        help_text="History of price negotiations for this trip"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    pre_ride_reminder_sent = models.BooleanField(
        default=False,
        help_text="Has the T-10 pre-ride reminder been sent for this trip?",
    )

    class Meta:
        indexes = [
            models.Index(fields=['trip_date']),
            models.Index(fields=['departure_time']),
            models.Index(fields=['trip_status']),
            models.Index(fields=['route', 'trip_date']),
            models.Index(fields=['driver']),
            models.Index(fields=['vehicle']),
        ]
        ordering = ['trip_date', 'departure_time']

    def __str__(self):
        return f"Trip {self.trip_id}: {self.route.route_name} on {self.trip_date}"
    
    @property
    def bookings(self):
        """Get all bookings for this trip"""
        return self.trip_bookings.all()
    
    @property
    def occupied_seats(self):
        """Get number of occupied seats"""
        return self.total_seats - self.available_seats
    
    @property
    def is_full(self):
        """Check if trip is at maximum capacity"""
        return self.available_seats <= 0
    
    @property
    def chat_group(self):
        """Get or create chat group for this trip"""
        from .models_chat import TripChatGroup
        chat_group, created = TripChatGroup.objects.get_or_create(
            trip=self,
            defaults={
                'group_name': f"Trip {self.trip_id} - {self.route.route_name}",
                'group_description': f"Group chat for trip from {self.route.first_stop.stop_name} to {self.route.last_stop.stop_name}",
                'created_by': self.driver
            }
        )
        return chat_group
    
    def clean(self):
        """Validate trip data"""
        if self.total_seats <= 0:
            raise ValidationError({'total_seats': 'Total seats must be greater than 0.'})
        
        if self.available_seats > self.total_seats:
            raise ValidationError({'available_seats': 'Available seats cannot exceed total seats.'})
        
        if self.available_seats < 0:
            raise ValidationError({'available_seats': 'Available seats cannot be negative.'})
        
        if self.base_fare <= 0:
            raise ValidationError({'base_fare': 'Base fare must be greater than 0.'})
        
        # Check if departure time is before arrival time
        if self.departure_time and self.estimated_arrival_time:
            if self.departure_time >= self.estimated_arrival_time:
                raise ValidationError('Departure time must be before estimated arrival time.')
    
    def save(self, *args, **kwargs):
        """Override save to ensure available_seats doesn't exceed total_seats"""
        if self.available_seats > self.total_seats:
            self.available_seats = self.total_seats
        super().save(*args, **kwargs)
    
    def start_trip(self, started_by=None):
        """Start the trip"""
        if self.trip_status != 'SCHEDULED':
            raise ValidationError('Only scheduled trips can be started.')
        
        self.trip_status = 'IN_PROGRESS'
        now = timezone.now()
        self.actual_departure_time = now.time()
        self.started_at = now
        if started_by is not None:
            self.started_by_user = started_by
        self.save()
        
        # Send system message to chat
        try:
            self.chat_group.send_system_message("🚌 Trip has started!")
        except:
            pass  # Chat group might not exist yet
    
    def complete_trip(self):
        """Complete the trip"""
        if self.trip_status not in ['SCHEDULED', 'IN_PROGRESS']:
            raise ValidationError('Only scheduled or in-progress trips can be completed.')
        
        self.trip_status = 'COMPLETED'
        self.actual_arrival_time = timezone.now().time()
        self.completed_at = timezone.now()
        self.save()
        
        # Archive chat group
        try:
            self.chat_group.archive()
            self.chat_group.send_system_message("✅ Trip completed! This chat will be archived.")
        except:
            pass
    
    def cancel_trip(self, reason=None):
        """Cancel the trip"""
        if self.trip_status == 'COMPLETED':
            raise ValidationError('Completed trips cannot be cancelled.')
        
        self.trip_status = 'CANCELLED'
        self.cancellation_reason = reason
        self.cancelled_at = timezone.now()
        self.save()
        
        # Send cancellation message to chat
        try:
            self.chat_group.send_system_message(f"❌ Trip cancelled: {reason or 'No reason provided'}")
        except:
            pass

class TripVehicleHistory(models.Model):
    """Model to preserve vehicle data even when vehicle is deleted"""
    trip = models.OneToOneField(Trip, on_delete=models.CASCADE, related_name='vehicle_history')
    vehicle = models.ForeignKey('Vehicle', on_delete=models.SET_NULL, null=True, blank=True)
    
    # Vehicle details (copied from vehicle at time of trip)
    vehicle_type = models.CharField(max_length=50, help_text="Type of vehicle (BUS, SHUTTLE, etc.)")
    vehicle_model = models.CharField(max_length=100, help_text="Model of the vehicle")
    vehicle_make = models.CharField(max_length=100, help_text="Make of the vehicle")
    vehicle_year = models.IntegerField(null=True, blank=True, help_text="Year of manufacture")
    vehicle_color = models.CharField(max_length=50, null=True, blank=True, help_text="Color of the vehicle")
    license_plate = models.CharField(max_length=20, help_text="License plate number")
    vehicle_capacity = models.IntegerField(help_text="Maximum capacity of the vehicle")
    vehicle_features = models.JSONField(
        default=dict, 
        blank=True,
        help_text="Additional features like AC, WiFi, etc."
    )
    vehicle_photo_url = models.URLField(
        null=True, 
        blank=True,
        help_text="URL to vehicle photo"
    )
    
    # Additional vehicle info
    fuel_type = models.CharField(
        max_length=20,
        null=True,
        blank=True,
        help_text="Type of fuel used"
    )
    engine_number = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Engine number"
    )
    chassis_number = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Chassis number"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['trip']),
            models.Index(fields=['vehicle']),
        ]

    def __str__(self):
        return f"Vehicle history for Trip {self.trip.trip_id}"
    
    def copy_from_vehicle(self, vehicle):
        """Copy data from a vehicle object"""
        self.vehicle = vehicle
        self.vehicle_type = vehicle.vehicle_type
        self.vehicle_model = vehicle.model_number
        self.vehicle_make = vehicle.company_name
        self.vehicle_color = vehicle.color
        self.license_plate = vehicle.plate_number
        self.vehicle_capacity = vehicle.seats or 1
        self.fuel_type = vehicle.fuel_type
        self.engine_number = vehicle.engine_number
        self.chassis_number = vehicle.chassis_number
        
        # Create features dict
        self.vehicle_features = {
            'type': vehicle.vehicle_type,
            'seats': vehicle.seats,
            'fuel_type': vehicle.fuel_type,
        }
        
        # Handle photos (you might want to store them differently)
        if vehicle.photo_front:
            self.vehicle_photo_url = f"/media/vehicles/{vehicle.id}_front.jpg"
        
        self.save()


class TripStopBreakdown(models.Model):
    """Model for storing individual stop breakdown data from frontend calculations"""
    trip = models.ForeignKey(Trip, on_delete=models.CASCADE, related_name='stop_breakdowns')
    from_stop_order = models.IntegerField(help_text="Order of pickup stop")
    to_stop_order = models.IntegerField(help_text="Order of drop-off stop")
    from_stop_name = models.CharField(max_length=100, help_text="Name of pickup stop")
    to_stop_name = models.CharField(max_length=100, help_text="Name of drop-off stop")
    
    # Distance and duration
    distance_km = models.DecimalField(
        max_digits=8, 
        decimal_places=2,
        help_text="Distance between stops in kilometers"
    )
    duration_minutes = models.IntegerField(help_text="Estimated duration between stops in minutes")
    
    # Pricing
    price = models.IntegerField(
        help_text="Price for this route segment"
    )
    
    # Coordinates
    from_latitude = models.DecimalField(
        max_digits=10, 
        decimal_places=8, 
        null=True, 
        blank=True,
        help_text="Latitude of pickup stop"
    )
    from_longitude = models.DecimalField(
        max_digits=11, 
        decimal_places=8, 
        null=True, 
        blank=True,
        help_text="Longitude of pickup stop"
    )
    to_latitude = models.DecimalField(
        max_digits=10, 
        decimal_places=8, 
        null=True, 
        blank=True,
        help_text="Latitude of drop-off stop"
    )
    to_longitude = models.DecimalField(
        max_digits=11, 
        decimal_places=8, 
        null=True, 
        blank=True,
        help_text="Longitude of drop-off stop"
    )
    
    # Calculation breakdown
    price_breakdown = models.JSONField(
        default=dict,
        blank=True,
        help_text="Detailed price calculation factors for this segment"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        unique_together = ['trip', 'from_stop_order', 'to_stop_order']
        indexes = [
            models.Index(fields=['trip']),
            models.Index(fields=['from_stop_order']),
            models.Index(fields=['to_stop_order']),
        ]
        ordering = ['trip', 'from_stop_order']
    
    def __str__(self):
        return f"Trip {self.trip.trip_id}: {self.from_stop_name} → {self.to_stop_name} (₨{self.price})"
    
    def clean(self):
        """Validate stop breakdown data"""
        if self.from_stop_order >= self.to_stop_order:
            raise ValidationError('Pickup stop must come before drop-off stop.')
        
        if self.distance_km <= 0:
            raise ValidationError({'distance_km': 'Distance must be greater than 0.'})
        
        if self.duration_minutes <= 0:
            raise ValidationError({'duration_minutes': 'Duration must be greater than 0.'})
        
        if self.price <= 0:
            raise ValidationError({'price': 'Price must be greater than 0.'})


class TripLiveLocationUpdate(models.Model):
    trip = models.ForeignKey('Trip', on_delete=models.CASCADE, related_name='live_location_updates')
    user = models.ForeignKey('UsersData', on_delete=models.CASCADE, related_name='live_location_updates')
    booking = models.ForeignKey('Booking', on_delete=models.SET_NULL, null=True, blank=True, related_name='live_location_updates')
    role = models.CharField(max_length=16)
    latitude = models.DecimalField(max_digits=10, decimal_places=8)
    longitude = models.DecimalField(max_digits=11, decimal_places=8)
    speed_mps = models.FloatField(null=True, blank=True)
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['trip', 'recorded_at']),
            models.Index(fields=['user', 'recorded_at']),
            models.Index(fields=['booking', 'recorded_at']),
            models.Index(fields=['role', 'recorded_at']),
        ]
        ordering = ['-recorded_at']


class RideAuditEvent(models.Model):
    trip = models.ForeignKey('Trip', on_delete=models.CASCADE, related_name='audit_events')
    booking = models.ForeignKey('Booking', on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_events')
    actor = models.ForeignKey('UsersData', on_delete=models.SET_NULL, null=True, blank=True, related_name='audit_events')
    event_type = models.CharField(max_length=64)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=['trip', 'created_at']),
            models.Index(fields=['booking', 'created_at']),
            models.Index(fields=['actor', 'created_at']),
            models.Index(fields=['event_type', 'created_at']),
        ]
        ordering = ['-created_at']