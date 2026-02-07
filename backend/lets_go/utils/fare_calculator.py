# """
# Fare calculation utilities for the bus/shuttle service
# """
# from decimal import Decimal
# from datetime import datetime, time
# from typing import Dict, List, Optional, Tuple
# from django.utils import timezone

# def is_peak_hour(current_time: time) -> bool:
#     """
#     Determine if current time is peak hour
    
#     Peak hours are typically:
#     - Morning: 7:00 AM - 9:00 AM
#     - Evening: 5:00 PM - 7:00 PM
#     """
#     morning_start = time(7, 0)  # 7:00 AM
#     morning_end = time(9, 0)    # 9:00 AM
#     evening_start = time(17, 0) # 5:00 PM
#     evening_end = time(19, 0)   # 7:00 PM
    
#     return (
#         (morning_start <= current_time <= morning_end) or
#         (evening_start <= current_time <= evening_end)
#     )

# def calculate_distance_fare(
#     from_stop_order: int,
#     to_stop_order: int,
#     fare_matrix: Dict[Tuple[int, int], Dict],
#     is_peak_hour: bool = False
# ) -> Decimal:
#     """
#     Calculate fare based on distance between stops
    
#     Args:
#         from_stop_order: Pickup stop order number
#         to_stop_order: Drop-off stop order number
#         fare_matrix: Dictionary mapping (from_order, to_order) to fare data
#         is_peak_hour: Whether current time is peak hour
    
#     Returns:
#         Calculated fare amount
#     """
#     if from_stop_order >= to_stop_order:
#         raise ValueError("Pickup stop must come before drop-off stop")
    
#     # Look up fare in matrix
#     fare_key = (from_stop_order, to_stop_order)
#     if fare_key not in fare_matrix:
#         raise ValueError(f"No fare defined for route segment {from_stop_order} to {to_stop_order}")
    
#     fare_data = fare_matrix[fare_key]
    
#     # Return appropriate fare based on time
#     if is_peak_hour:
#         return Decimal(str(fare_data['peak_fare']))
#     else:
#         return Decimal(str(fare_data['off_peak_fare']))

# def calculate_booking_fare(
#     from_stop_order: int,
#     to_stop_order: int,
#     number_of_seats: int,
#     fare_matrix: Dict[Tuple[int, int], Dict],
#     booking_time: Optional[datetime] = None,
#     base_fare_multiplier: float = 1.0,
#     seat_discount: float = 0.0
# ) -> Dict[str, any]:
#     """
#     Calculate total fare for a booking
    
#     Args:
#         from_stop_order: Pickup stop order number
#         to_stop_order: Drop-off stop order number
#         number_of_seats: Number of seats being booked
#         fare_matrix: Dictionary mapping (from_order, to_order) to fare data
#         booking_time: Time of booking (for peak hour calculation)
#         base_fare_multiplier: Multiplier for base fare (for special pricing)
#         seat_discount: Discount per seat for multiple seats (0.0 to 1.0)
    
#     Returns:
#         Dictionary with fare breakdown
#     """
#     if booking_time is None:
#         booking_time = timezone.now()
    
#     # Determine if peak hour
#     current_time = booking_time.time()
#     peak_hour = is_peak_hour(current_time)
    
#     # Calculate base fare for one seat
#     base_fare = calculate_distance_fare(
#         from_stop_order, 
#         to_stop_order, 
#         fare_matrix, 
#         peak_hour
#     )
    
#     # Apply base fare multiplier
#     adjusted_base_fare = base_fare * Decimal(str(base_fare_multiplier))
    
#     # Calculate seat discount
#     if number_of_seats > 1 and seat_discount > 0:
#         discount_per_seat = adjusted_base_fare * Decimal(str(seat_discount))
#         fare_per_seat = adjusted_base_fare - discount_per_seat
#     else:
#         fare_per_seat = adjusted_base_fare
    
#     # Calculate total fare
#     total_fare = fare_per_seat * number_of_seats
    
#     # Prepare breakdown
#     breakdown = {
#         'base_fare_per_seat': float(base_fare),
#         'adjusted_base_fare_per_seat': float(adjusted_base_fare),
#         'fare_per_seat': float(fare_per_seat),
#         'number_of_seats': number_of_seats,
#         'total_fare': float(total_fare),
#         'is_peak_hour': peak_hour,
#         'base_fare_multiplier': base_fare_multiplier,
#         'seat_discount_applied': seat_discount > 0 and number_of_seats > 1,
#         'discount_per_seat': float(adjusted_base_fare * Decimal(str(seat_discount))) if seat_discount > 0 and number_of_seats > 1 else 0.0,
#         'distance_km': fare_matrix.get((from_stop_order, to_stop_order), {}).get('distance_km', 0),
#         'calculation_time': booking_time.isoformat()
#     }
    
#     return breakdown

# def get_fare_matrix_for_route(route_id: int) -> Dict[Tuple[int, int], Dict]:
#     """
#     Get fare matrix for a specific route
    
#     Args:
#         route_id: ID of the route
    
#     Returns:
#         Dictionary mapping (from_order, to_order) to fare data
#     """
#     from ..models import FareMatrix
    
#     fare_matrix = {}
#     fares = FareMatrix.objects.filter(
#         route_id=route_id,
#         is_active=True
#     ).select_related('from_stop', 'to_stop')
    
#     for fare in fares:
#         key = (fare.from_stop.stop_order, fare.to_stop.stop_order)
#         fare_matrix[key] = {
#             'base_fare': float(fare.base_fare),
#             'peak_fare': float(fare.peak_fare),
#             'off_peak_fare': float(fare.off_peak_fare),
#             'distance_km': float(fare.distance_km),
#             'from_stop_name': fare.from_stop.stop_name,
#             'to_stop_name': fare.to_stop.stop_name
#         }
    
#     return fare_matrix

# def validate_fare_calculation(
#     from_stop_order: int,
#     to_stop_order: int,
#     fare_matrix: Dict[Tuple[int, int], Dict]
# ) -> List[str]:
#     """
#     Validate fare calculation parameters
    
#     Args:
#         from_stop_order: Pickup stop order number
#         to_stop_order: Drop-off stop order number
#         fare_matrix: Fare matrix dictionary
    
#     Returns:
#         List of validation errors (empty if valid)
#     """
#     errors = []
    
#     if from_stop_order >= to_stop_order:
#         errors.append("Pickup stop must come before drop-off stop")
    
#     fare_key = (from_stop_order, to_stop_order)
#     if fare_key not in fare_matrix:
#         errors.append(f"No fare defined for route segment {from_stop_order} to {to_stop_order}")
    
#     return errors

# def get_available_seats_for_trip(trip_id: int) -> List[int]:
#     """
#     Get list of available seat numbers for a trip
    
#     Args:
#         trip_id: ID of the trip
    
#     Returns:
#         List of available seat numbers
#     """
#     from ..models import Trip, SeatAssignment
    
#     try:
#         trip = Trip.objects.get(id=trip_id)
#         total_seats = trip.total_seats
        
#         # Get occupied seats
#         occupied_seats = SeatAssignment.objects.filter(
#             trip_id=trip_id
#         ).values_list('seat_number', flat=True)
        
#         # Return available seats
#         all_seats = set(range(1, total_seats + 1))
#         occupied_seats_set = set(occupied_seats)
#         available_seats = sorted(list(all_seats - occupied_seats_set))
        
#         return available_seats
    
#     except Trip.DoesNotExist:
#         return []

# def calculate_route_statistics(route_id: int) -> Dict[str, any]:
#     """
#     Calculate statistics for a route
    
#     Args:
#         route_id: ID of the route
    
#     Returns:
#         Dictionary with route statistics
#     """
#     from ..models import Route, RouteStop, FareMatrix, Trip, Booking
    
#     try:
#         route = Route.objects.get(id=route_id)
#         stops = route.route_stops.all().order_by('stop_order')
#         fares = route.fare_matrix.all()
#         trips = route.trips.all()
#         bookings = Booking.objects.filter(trip__route_id=route_id)
        
#         # Calculate statistics
#         total_stops = stops.count()
#         total_fare_segments = fares.count()
#         total_trips = trips.count()
#         total_bookings = bookings.count()
        
#         # Calculate average fare
#         if fares.exists():
#             from django.db import models
#             avg_base_fare = fares.aggregate(
#                 avg_base=models.Avg('base_fare'),
#                 avg_peak=models.Avg('peak_fare'),
#                 avg_off_peak=models.Avg('off_peak_fare')
#             )
#         else:
#             avg_base_fare = {'avg_base': 0, 'avg_peak': 0, 'avg_off_peak': 0}
        
#         # Calculate total revenue
#         total_revenue = bookings.aggregate(
#             total=models.Sum('total_fare')
#         )['total'] or 0
        
#         return {
#             'route_id': route_id,
#             'route_name': route.route_name,
#             'total_stops': total_stops,
#             'total_fare_segments': total_fare_segments,
#             'total_trips': total_trips,
#             'total_bookings': total_bookings,
#             'total_revenue': float(total_revenue),
#             'average_fares': {
#                 'base': float(avg_base_fare['avg_base'] or 0),
#                 'peak': float(avg_base_fare['avg_peak'] or 0),
#                 'off_peak': float(avg_base_fare['avg_off_peak'] or 0)
#             },
#             'route_distance_km': float(route.total_distance_km or 0),
#             'estimated_duration_minutes': route.estimated_duration_minutes or 0
#         }
    
#     except Route.DoesNotExist:
#         return {} 