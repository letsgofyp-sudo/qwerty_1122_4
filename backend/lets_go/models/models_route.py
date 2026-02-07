from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError

class Route(models.Model):
    """Model for predefined bus/shuttle routes"""
    route_id = models.CharField(max_length=50, unique=True, help_text="Unique route identifier like R001")
    route_name = models.CharField(max_length=100, help_text="Display name for the route")
    route_description = models.TextField(null=True, blank=True, help_text="Detailed description of the route")
    total_distance_km = models.DecimalField(
        max_digits=8, 
        decimal_places=2, 
        null=True, 
        blank=True,
        validators=[MinValueValidator(0.1)],
        help_text="Total route distance in kilometers"
    )
    estimated_duration_minutes = models.IntegerField(
        null=True, 
        blank=True,
        validators=[MinValueValidator(1)],
        help_text="Estimated travel time in minutes"
    )
    # Optional dense polyline geometry (list of {lat, lng}) for map display
    route_geometry = models.JSONField(
        default=list,
        blank=True,
        help_text="Dense route polyline coordinates for map display (list of {lat, lng} points)"
    )
    is_active = models.BooleanField(default=True, help_text="Whether this route is available for booking")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['route_id']),
            models.Index(fields=['is_active']),
            models.Index(fields=['created_at']),
        ]
        ordering = ['route_name']

    def __str__(self):
        return f"Route {self.route_id}: {self.route_name}"
    
    @property
    def stops(self):
        """Get all stops in order"""
        return self.route_stops.all().order_by('stop_order')
    
    @property
    def first_stop(self):
        """Get first stop"""
        return self.stops.first()
    
    @property
    def last_stop(self):
        """Get last stop"""
        return self.stops.last()
    
    def clean(self):
        """Validate route data"""
        if self.total_distance_km and self.total_distance_km <= 0:
            raise ValidationError({'total_distance_km': 'Distance must be greater than 0.'})
        
        if self.estimated_duration_minutes and self.estimated_duration_minutes <= 0:
            raise ValidationError({'estimated_duration_minutes': 'Duration must be greater than 0.'})

class RouteStop(models.Model):
    """Model for stops along a route"""
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name='route_stops')
    stop_name = models.CharField(max_length=100, help_text="Name of the stop")
    stop_order = models.IntegerField(
        validators=[MinValueValidator(1)],
        help_text="Order of this stop in the route (1, 2, 3, ...)"
    )
    latitude = models.DecimalField(
        max_digits=10, 
        decimal_places=8, 
        null=True, 
        blank=True,
        help_text="GPS latitude coordinate"
    )
    longitude = models.DecimalField(
        max_digits=11, 
        decimal_places=8, 
        null=True, 
        blank=True,
        help_text="GPS longitude coordinate"
    )
    address = models.TextField(null=True, blank=True, help_text="Full address of the stop")
    estimated_time_from_start = models.IntegerField(
        null=True, 
        blank=True,
        validators=[MinValueValidator(0)],
        help_text="Estimated minutes from route start to this stop"
    )
    is_active = models.BooleanField(default=True, help_text="Whether this stop is active")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['route', 'stop_order']
        indexes = [
            models.Index(fields=['route', 'stop_order']),
            models.Index(fields=['is_active']),
        ]
        ordering = ['route', 'stop_order']

    def __str__(self):
        return f"{self.route.route_name} - Stop {self.stop_order}: {self.stop_name}"
    
    def clean(self):
        """Validate stop data"""
        if self.stop_order <= 0:
            raise ValidationError({'stop_order': 'Stop order must be greater than 0.'})
        
        if self.estimated_time_from_start and self.estimated_time_from_start < 0:
            raise ValidationError({'estimated_time_from_start': 'Time from start cannot be negative.'})
        
        # Check if stop order is unique within the route
        if self.pk is None:  # New instance
            if RouteStop.objects.filter(route=self.route, stop_order=self.stop_order).exists():
                raise ValidationError({'stop_order': f'Stop order {self.stop_order} already exists for this route.'})