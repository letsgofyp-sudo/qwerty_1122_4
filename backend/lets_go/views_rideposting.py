from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse, HttpResponse, Http404
from django.db import connection
from django.db.utils import OperationalError, IntegrityError
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import datetime, timedelta, time
import json
import random
from django.db.models import Prefetch, Count, Q
import time as pytime
from .models import UsersData, Vehicle, Trip, Route, RouteStop, TripStopBreakdown, Booking
# from .utils.fare_calculator import is_peak_hour, get_fare_matrix_for_route, calculate_booking_fare
from .views_notifications import send_ride_notification_async
from decimal import Decimal
from .utils.route_geometry import update_route_geometry_from_stops
from .utils.verification_guard import verification_block_response, ride_create_block_response


def _to_int_pkr(value, default=None):
    if value is None:
        return default
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default

@csrf_exempt
def create_trip(request):
    """Create a new trip with enhanced fare calculation"""
    if request.method == 'POST':
        try:
            print("=== CREATE_TRIP DEBUG START ===")
            print(f"Request body: {request.body}")
            
            data = json.loads(request.body)
            print(f"Parsed JSON data: {data}")

            # Extract trip data
            route_id = data.get('route_id')
            vehicle_id = data.get('vehicle_id')
            departure_time = data.get('departure_time')
            trip_date_str = data.get('trip_date')
            total_seats = data.get('total_seats', 1)
            notes = data.get('notes', '')
            gender_preference = data.get('gender_preference', 'Any')
            
            print(f"Extracted data:")
            print(f"  route_id: {route_id} (type: {type(route_id)})")
            print(f"  vehicle_id: {vehicle_id} (type: {type(vehicle_id)})")
            print(f"  departure_time: {departure_time}")
            print(f"  trip_date_str: {trip_date_str}")
            print(f"  total_seats: {total_seats}")
            print(f"  notes: {notes}")
            print(f"  gender_preference: {gender_preference}")
            
            # Get route and vehicle (lightweight to avoid loading large blobs)
            print("=== LOOKING UP ROUTE AND VEHICLE ===")
            try:
                try:
                    connection.close_if_unusable_or_obsolete()
                except Exception:
                    pass
                print(f"Looking for route with route_id: {route_id}")
                route = (
                    Route.objects
                    .only('id', 'route_id', 'route_name')
                    .get(route_id=route_id)
                )
                print(f"Route found: {route.route_name} (ID: {route.id})")
                
                print(f"Looking for vehicle with id: {vehicle_id}")
                vehicle = (
                    Vehicle.objects
                    .only('id', 'model_number', 'company_name', 'plate_number', 'vehicle_type', 'color', 'seats', 'fuel_type', 'status')
                    .defer('photo_front', 'photo_back', 'documents_image')
                    .get(id=vehicle_id)
                )
                print(f"Vehicle found: {vehicle.model_number} (ID: {vehicle.id})")
            except (Route.DoesNotExist, Vehicle.DoesNotExist) as e:
                print(f"Route or vehicle not found: {e}")
                return JsonResponse({
                    'success': False,
                    'error': 'Route or vehicle not found'
                }, status=404)

            if getattr(vehicle, 'status', Vehicle.STATUS_VERIFIED) != Vehicle.STATUS_VERIFIED:
                return JsonResponse({
                    'success': False,
                    'error': 'Selected vehicle is not verified yet. Please wait for admin verification.'
                }, status=400)
            
            # Parse departure time
            print("=== PARSING DEPARTURE TIME ===")
            try:
                print(f"Parsing departure time: {departure_time}")
                departure_time_obj = datetime.strptime(departure_time, '%H:%M').time()
                print(f"Parsed departure time: {departure_time_obj}")
            except ValueError as e:
                print(f"Error parsing departure time: {e}")
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid departure time format. Use HH:MM'
                }, status=400)

            # Parse trip date
            print("=== PARSING TRIP DATE ===")
            if trip_date_str:
                try:
                    print(f"Parsing trip date: {trip_date_str}")
                    trip_date = datetime.strptime(trip_date_str, '%Y-%m-%d').date()
                    print(f"Parsed trip date: {trip_date}")
                except ValueError as e:
                    print(f"Error parsing trip date: {e}")
                    return JsonResponse({
                        'success': False,
                        'error': 'Invalid trip date format. Use YYYY-MM-DD'
                    }, status=400)
            else:
                trip_date = datetime.now().date()
                print(f"Using current date: {trip_date}")

            # Enforce that trip start time is at least 15 minutes in the future.
            # We intentionally use naive datetimes here so that we compare in the
            # same clock domain as the (date, time) values sent by the client.
            now = datetime.now()
            trip_start = datetime.combine(trip_date, departure_time_obj)

            min_start = now + timedelta(minutes=15)
            print(f"Current time (naive): {now}, requested trip_start: {trip_start}, min_start_allowed: {min_start}")
            if trip_start < min_start:
                return JsonResponse({
                    'success': False,
                    'error': 'Trip must start at least 15 minutes after current time so passengers have time to book.'
                }, status=400)
            
            # Get custom price from frontend; fare is now fully client-calculated
            print("=== PROCESSING FARE (CLIENT-DRIVEN) ===")
            custom_price = data.get('custom_price')
            if custom_price is None:
                print("custom_price is missing in request body")
                return JsonResponse({
                    'success': False,
                    'error': 'custom_price is required; fare must be calculated on the client.'
                }, status=400)

            base_fare_value = _to_int_pkr(custom_price)
            if base_fare_value is None:
                print(f"Invalid custom_price value: {custom_price}")
                return JsonResponse({
                    'success': False,
                    'error': 'custom_price must be a numeric value'
                }, status=400)

            # Minimal fare_data wrapper so downstream code can still store metadata
            fare_data = {
                'base_fare': base_fare_value,
                'total_distance_km': float(route.total_distance_km) if getattr(route, 'total_distance_km', None) else 0.0,
                'calculation_breakdown': {
                    'source': 'client',
                },
            }
            
            # Get driver from request data (since we're not using Django's built-in auth)
            print("=== LOOKING UP DRIVER ===")
            driver_id = data.get('driver_id')
            print(f"Driver ID from request: {driver_id}")
            
            if not driver_id:
                print("Driver ID is missing")
                return JsonResponse({
                    'success': False,
                    'error': 'Driver ID is required'
                }, status=400)
            
            try:
                print(f"Looking for driver with id: {driver_id}")
                # Fetch minimal user fields; defer all binary/image fields to avoid heavy loads
                driver = (
                    UsersData.objects
                    .only('id', 'name', 'status')
                    .defer(
                        'profile_photo', 'live_photo',
                        'cnic_front_image', 'cnic_back_image',
                        'driving_license_front', 'driving_license_back',
                        'accountqr'
                    )
                    .get(id=driver_id)
                )
                print(f"Driver found: {driver.name} (ID: {driver.id})")
            except UsersData.DoesNotExist as e:
                print(f"Driver not found: {e}")
                return JsonResponse({
                    'success': False,
                    'error': 'Driver not found'
                }, status=404)

            blocked = ride_create_block_response(driver.id)
            if blocked is not None:
                return blocked
            
            # Create trip
            print("=== CREATING TRIP ===")
            try:
                print("Calculating estimated arrival time...")
                estimated_arrival = calculate_estimated_arrival(departure_time_obj, route)
                print(f"Estimated arrival time: {estimated_arrival}")
                
                print("Creating trip object...")
                trip = Trip.objects.create(
                    trip_id=f"T{random.randint(100, 999)}-{datetime.now().strftime('%Y-%m-%d-%H:%M')}",
                    route=route,
                    vehicle=vehicle,
                    driver=driver,
                    trip_date=trip_date,
                    departure_time=departure_time_obj,
                    estimated_arrival_time=estimated_arrival,
                    total_seats=total_seats,
                    available_seats=total_seats,
                    base_fare=fare_data['base_fare'],
                    total_distance_km=fare_data.get('total_distance_km'),
                    total_duration_minutes=fare_data.get('total_duration_minutes'),
                    fare_calculation=fare_data,
                    notes=notes,
                    gender_preference=gender_preference,
                    is_negotiable=data.get('is_negotiable', True),
                    minimum_acceptable_fare=_to_int_pkr(data.get('minimum_acceptable_fare'), default=None),
                )
                print(f"Trip created successfully: {trip.trip_id}")
            except Exception as e:
                print(f"Error creating trip: {e}")
                import traceback
                traceback.print_exc()
                return JsonResponse({
                    'success': False,
                    'error': f'Error creating trip: {str(e)}'
                }, status=500)
            
            # Create vehicle history
            print("=== CREATING VEHICLE HISTORY ===")
            try:
                from .models import TripVehicleHistory
                print("Creating vehicle history...")
                
                # First check if vehicle history already exists
                try:
                    vehicle_history = TripVehicleHistory.objects.get(trip=trip)
                    print("Vehicle history already exists, updating...")
                    vehicle_history.copy_from_vehicle(vehicle)
                except TripVehicleHistory.DoesNotExist:
                    print("Creating new vehicle history...")
                    # Create with required fields from vehicle
                    seats_for_history = (vehicle.seats if vehicle.vehicle_type == Vehicle.FOUR_WHEELER else 2)
                    vehicle_history = TripVehicleHistory.objects.create(
                        trip=trip,
                        vehicle=vehicle,
                        vehicle_type=vehicle.vehicle_type,
                        vehicle_model=vehicle.model_number,
                        vehicle_make=vehicle.company_name,
                        vehicle_color=vehicle.color,
                        license_plate=vehicle.plate_number,
                        vehicle_capacity=seats_for_history or 1,
                        fuel_type=vehicle.fuel_type,
                        engine_number=vehicle.engine_number,
                        chassis_number=vehicle.chassis_number,
                        vehicle_features={
                            'type': vehicle.vehicle_type,
                            'seats': seats_for_history,
                            'fuel_type': vehicle.fuel_type,
                        }
                    )
                    print("Vehicle history created successfully")
            except Exception as e:
                print(f"Error creating vehicle history: {e}")
                import traceback
                traceback.print_exc()
                # Don't fail the entire request for vehicle history error
                print("Continuing without vehicle history...")
            
            # Create stop breakdowns if provided in request data
            print("=== CREATING STOP BREAKDOWNS ===")
            try:
                if 'stop_breakdown' in data and data['stop_breakdown']:
                    print(f"Creating {len(data['stop_breakdown'])} stop breakdown records...")
                    for stop_data in data['stop_breakdown']:
                        TripStopBreakdown.objects.create(
                            trip=trip,
                            from_stop_order=stop_data.get('from_stop'),
                            to_stop_order=stop_data.get('to_stop'),
                            from_stop_name=stop_data.get('from_stop_name'),
                            to_stop_name=stop_data.get('to_stop_name'),
                            distance_km=stop_data.get('distance'),
                            duration_minutes=stop_data.get('duration'),
                            price=stop_data.get('price'),
                            from_latitude=stop_data.get('from_coordinates', {}).get('lat'),
                            from_longitude=stop_data.get('from_coordinates', {}).get('lng'),
                            to_latitude=stop_data.get('to_coordinates', {}).get('lat'),
                            to_longitude=stop_data.get('to_coordinates', {}).get('lng'),
                            price_breakdown=stop_data.get('price_breakdown', {}),
                        )
                    print("Stop breakdowns created successfully")
                else:
                    print("No stop breakdown data provided in request")
            except Exception as e:
                print(f"Error creating stop breakdowns: {e}")
                import traceback
                traceback.print_exc()
                # Don't fail the entire request for stop breakdown error
                print("Continuing without stop breakdowns...")
            
            print("=== CREATE_TRIP SUCCESS ===")
            return JsonResponse({
                'success': True,
                'message': 'Trip created successfully',
                'trip_id': trip.trip_id,
                'custom_price': fare_data['base_fare'],
                'fare_data': fare_data
            }, status=201)
            
        except json.JSONDecodeError as e:
            print(f"JSON decode error: {e}")
            return JsonResponse({
                'success': False,
                'error': 'Invalid JSON data'
            }, status=400)
        except Exception as e:
            print(f"=== CREATE_TRIP GENERAL ERROR ===")
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            return JsonResponse({
                'success': False,
                'error': f'Failed to create trip: {str(e)}'
            }, status=500)
    
    return JsonResponse({
        'success': False,
        'error': 'Only POST method allowed'
    }, status=405)

# ================= Driver request management endpoints =================

@csrf_exempt
def cancel_booking(request, booking_id: int):
    """Cancel a passenger booking."""

    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Method not allowed'}, status=405)

    try:
        try:
            data = json.loads(request.body.decode('utf-8') or '{}')
        except Exception:
            data = {}

        reason = data.get('reason', 'Cancelled by passenger')

        # We treat booking_id as the primary key ID, which is what the Flutter
        # app sends via ApiService.cancelBooking(bookingId, reason).
        booking = (
            Booking.objects
            .select_related('trip', 'trip__driver', 'passenger')
            .get(id=booking_id)
        )

        # For in-progress trips, allow cancel even if passenger is already on-board.
        # If already on-board, mark as cancelled on board (no seat release mid-trip).
        try:
            trip_status = getattr(getattr(booking, 'trip', None), 'trip_status', None)
            ride_status = getattr(booking, 'ride_status', None) or 'NOT_STARTED'
        except Exception:
            trip_status = None
            ride_status = 'NOT_STARTED'

        if trip_status == 'IN_PROGRESS' and ride_status != 'NOT_STARTED':
            now = timezone.now()
            booking.booking_status = 'CANCELLED'
            booking.ride_status = 'CANCELLED_ON_BOARD'
            booking.cancelled_at = now
            booking.updated_at = now
            booking.save(update_fields=['booking_status', 'ride_status', 'cancelled_at', 'updated_at'])
        else:
            # Use the model helper so seats and chat membership are handled
            booking.cancel_booking(reason=reason)

        # Notify the driver that this passenger cancelled (or cancelled on board)
        try:
            driver = getattr(booking.trip, 'driver', None)
            passenger = booking.passenger
            if driver and getattr(driver, 'id', None):
                event_type = 'booking_cancelled_by_passenger'
                title = 'Booking cancelled by passenger'
                body = f'{passenger.name} cancelled their booking for your trip {booking.trip.trip_id}. Their seats have been released for other passengers.'
                if getattr(booking.trip, 'trip_status', None) == 'IN_PROGRESS' and getattr(booking, 'ride_status', None) == 'CANCELLED_ON_BOARD':
                    event_type = 'passenger_cancelled_on_board'
                    title = 'Passenger cancelled on board'
                    body = f'{passenger.name} cancelled on board for trip {booking.trip.trip_id}.'
                payload = {
                    'user_id': str(driver.id),
                    'driver_id': str(driver.id),
                    'title': title,
                    'body': body,
                    'data': {
                        'type': event_type,
                        'trip_id': str(booking.trip.trip_id),
                        'booking_id': str(booking.id),
                    },
                }
                send_ride_notification_async(payload)
        except Exception as e:
            # Log but do not fail the cancellation if notification fails
            print('[cancel_booking][notify_driver][ERROR]:', e)

        msg = 'Booking cancelled successfully'
        if getattr(booking.trip, 'trip_status', None) == 'IN_PROGRESS' and getattr(booking, 'ride_status', None) == 'CANCELLED_ON_BOARD':
            msg = 'Passenger cancelled on board'

        return JsonResponse({
            'success': True,
            'message': msg,
        })

    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)
    except ValidationError as e:
        # E.g. booking cannot be cancelled due to status/trip rules
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        print('[cancel_booking][ERROR]:', e)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

@csrf_exempt
def create_route(request):
    if request.method == 'POST':
        try:
            import json
            data = json.loads(request.body.decode('utf-8'))
            
            # Extract route data
            coordinates = data.get('coordinates', [])
            location_names = data.get('location_names', [])
            route_points = data.get('route_points', [])
            
            if len(coordinates) < 2:
                return JsonResponse({'success': False, 'error': 'At least 2 coordinates required (origin and destination)'}, status=400)
            
            # Create route name from first and last location
            origin_name = location_names[0] if location_names else "Origin"
            destination_name = location_names[-1] if len(location_names) > 1 else "Destination"
            route_name = f"{origin_name} to {destination_name}"
            
            # Generate unique route ID
            import uuid
            route_id = f"R{str(uuid.uuid4())[:8].upper()}"
            
            # Create the route
            route = Route.objects.create(
                route_id=route_id,
                route_name=route_name,
                route_description=f"Route from {origin_name} to {destination_name}",
                is_active=True
            )
            
            # Create route stops from coordinates
            for i, coord in enumerate(coordinates):
                stop_name = location_names[i] if i < len(location_names) else f"Stop {i+1}"
                RouteStop.objects.create(
                    route=route,
                    stop_name=stop_name,
                    stop_order=i+1,
                    latitude=coord.get('lat'),
                    longitude=coord.get('lng'),
                    address=stop_name,
                    is_active=True
                )
            
            # Calculate total distance (simplified - sum of distances between consecutive points)
            normalized_stops = []
            for i, coord in enumerate(coordinates):
                normalized_stops.append({
                    'order': i + 1,
                    'name': location_names[i] if i < len(location_names) else f"Stop {i+1}",
                    'lat': coord.get('lat'),
                    'lng': coord.get('lng'),
                })

            total_distance = 0
            for i in range(len(coordinates) - 1):
                from_coord = coordinates[i]
                to_coord = coordinates[i + 1]
                distance = _calculate_distance(
                    from_coord.get('lat'), from_coord.get('lng'),
                    to_coord.get('lat'), to_coord.get('lng')
                )
                total_distance += distance
            
            # Update route with calculated distance
            route.total_distance_km = round(total_distance, 2)
            route.estimated_duration_minutes = int(total_distance * 2)  # Rough estimate: 2 min per km
            update_route_geometry_from_stops(route, normalized_stops)
            route.save()
            
            return JsonResponse({
                'success': True,
                'route': {
                    'id': route.route_id,
                    'name': route.route_name,
                    'distance': float(route.total_distance_km),
                    'duration': route.estimated_duration_minutes,
                    'stops_count': len(coordinates)
                }
            })
            
        except Exception as e:
            import traceback
            print('CREATE_ROUTE ERROR:', traceback.format_exc())
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)

def calculate_estimated_arrival(departure_time, route):
    """Calculate estimated arrival time based on route distance and average speed"""
    if not route.total_distance_km:
        # If no distance available, add 2 hours as default
        departure_minutes = departure_time.hour * 60 + departure_time.minute
        arrival_minutes = departure_minutes + 120  # 2 hours
        arrival_hour = (arrival_minutes // 60) % 24  # Ensure hour is within 0-23
        arrival_minute = arrival_minutes % 60
        return time(arrival_hour, arrival_minute)
    
    # Assume average speed of 50 km/h for better time estimates (was too slow at 30)
    average_speed_kmh = 50
    travel_time_hours = route.total_distance_km / average_speed_kmh
    travel_time_minutes = int(travel_time_hours * 60)
    
    departure_minutes = departure_time.hour * 60 + departure_time.minute
    arrival_minutes = departure_minutes + travel_time_minutes
    arrival_hour = (arrival_minutes // 60) % 24  # Ensure hour is within 0-23 range
    arrival_minute = arrival_minutes % 60
    
    print(f"Departure: {departure_time.hour}:{departure_time.minute}")
    print(f"Travel time: {travel_time_hours:.2f} hours ({travel_time_minutes} minutes)")
    print(f"Calculated arrival: {arrival_hour}:{arrival_minute:02d}")
    
    return time(arrival_hour, arrival_minute)

def _calculate_distance(lat1, lon1, lat2, lon2):
    """Calculate distance between two points using Haversine formula"""
    print(f"  _calculate_distance called with: lat1={lat1}, lon1={lon1}, lat2={lat2}, lon2={lon2}")
    
    try:
        from math import radians, cos, sin, asin, sqrt
        
        # Convert to radians
        lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
        print(f"  Converted to radians: lat1={lat1}, lon1={lon1}, lat2={lat2}, lon2={lon2}")
        
        # Haversine formula
        dlat = lat2 - lat1
        dlon = lon2 - lon1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        
        # Radius of earth in kilometers
        r = 6371
        
        distance = c * r
        print(f"  Calculated distance: {distance} km")
        return distance
    except Exception as e:
        print(f"  Error in _calculate_distance: {e}")
        return 0


@csrf_exempt
def get_trip_breakdown(request, trip_id):
    """Get detailed breakdown for a specific trip"""
    if request.method == 'GET':
        try:
            trip = Trip.objects.get(trip_id=trip_id)
            
            # Get stop breakdown data
            stop_breakdowns = trip.stop_breakdowns.all().order_by('from_stop_order')
            breakdown_list = []
            for breakdown in stop_breakdowns:
                breakdown_list.append({
                    'from_stop_order': breakdown.from_stop_order,
                    'to_stop_order': breakdown.to_stop_order,
                    'from_stop_name': breakdown.from_stop_name,
                    'to_stop_name': breakdown.to_stop_name,
                    'distance_km': float(breakdown.distance_km),
                    'duration_minutes': breakdown.duration_minutes,
                    'price': int(breakdown.price) if breakdown.price is not None else None,
                    'from_coordinates': {
                        'lat': float(breakdown.from_latitude) if breakdown.from_latitude else None,
                        'lng': float(breakdown.from_longitude) if breakdown.from_longitude else None,
                    },
                    'to_coordinates': {
                        'lat': float(breakdown.to_latitude) if breakdown.to_latitude else None,
                        'lng': float(breakdown.to_longitude) if breakdown.to_longitude else None,
                    },
                    'price_breakdown': breakdown.price_breakdown,
                })
            
            return JsonResponse({
                'success': True,
                'trip': {
                    'trip_id': trip.trip_id,
                    'total_distance_km': float(trip.total_distance_km) if trip.total_distance_km else None,
                    'total_duration_minutes': trip.total_duration_minutes,
                    'base_fare': int(trip.base_fare) if trip.base_fare is not None else 0,
                    'fare_calculation': trip.fare_calculation,
                    'stop_breakdown': breakdown_list,
                }
            })
        except Trip.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)

# Add these helper functions and views to the end of views.py

def map_trip_status_to_frontend(trip_status):
    """Map backend trip status to frontend expectations"""
    status_mapping = {
        'SCHEDULED': 'pending',
        'IN_PROGRESS': 'inprocess', 
        'COMPLETED': 'completed',
        'CANCELLED': 'cancelled'
    }
    return status_mapping.get(trip_status, 'unknown')

def update_trip_status_automatically(trip):
    """Automatically update trip status based on date/time"""
    now = timezone.now()
    trip_datetime = timezone.make_aware(
        datetime.combine(trip.trip_date, trip.departure_time)
    )
    
    # If trip is in the past and not completed/cancelled, mark as completed
    if now > trip_datetime and trip.trip_status == 'SCHEDULED':
        trip.trip_status = 'COMPLETED'
        trip.completed_at = now
        trip.save()
    # If trip is currently happening (within 2 hours of departure), mark as in progress
    elif (trip_datetime - timedelta(hours=2)) <= now <= (trip_datetime + timedelta(hours=8)) and trip.trip_status == 'SCHEDULED':
        trip.trip_status = 'IN_PROGRESS'
        trip.started_at = now
        trip.save()
    
    return trip

def can_edit_trip(trip):
    """Check if trip can be edited"""
    # Can't edit completed, in-progress, or cancelled trips
    if trip.trip_status in ['COMPLETED', 'IN_PROGRESS', 'CANCELLED']:
        return False
    
    # Can't edit if there are confirmed bookings
    confirmed_bookings = trip.trip_bookings.filter(booking_status='CONFIRMED')
    if confirmed_bookings.exists():
        return False
    
    return True

def can_delete_trip(trip):
    """Check if trip can be deleted"""
    # Can't delete completed, in-progress, or cancelled trips
    if trip.trip_status in ['COMPLETED', 'IN_PROGRESS', 'CANCELLED']:
        return False
    
    # Can't delete if there are any bookings
    if trip.trip_bookings.exists():
        return False
    
    return True

def can_cancel_trip(trip):
    """Check if trip can be cancelled"""
    # Can't cancel already cancelled or completed trips
    if trip.trip_status in ['CANCELLED', 'COMPLETED']:
        return False
    
    return True

@csrf_exempt
def get_user_rides(request, user_id):
    """Get all rides created by a specific user"""
    if request.method == 'GET':
        try:
            # Verify user exists with minimal fields
            user = UsersData.objects.only('id').get(id=user_id)

            # Pagination to avoid huge result sets
            try:
                limit = int(request.GET.get('limit', 20))
                limit = max(1, min(limit, 200))
            except Exception:
                limit = 20
            try:
                offset = int(request.GET.get('offset', 0))
                offset = max(0, offset)
            except Exception:
                offset = 0

            # Summary mode flag to return lightweight payload for My Rides list
            mode = (request.GET.get('mode') or '').lower()
            is_summary = mode == 'summary'

            # Prefetch minimal related data only when not in summary mode
            route_stops_prefetch = None
            stop_breakdowns_prefetch = None
            if not is_summary:
                route_stops_prefetch = Prefetch(
                    'route__route_stops',
                    queryset=RouteStop.objects.only('id', 'stop_order', 'stop_name', 'latitude', 'longitude', 'address', 'estimated_time_from_start').order_by('stop_order')
                )
                stop_breakdowns_prefetch = Prefetch(
                    'stop_breakdowns',
                    queryset=TripStopBreakdown.objects.only('trip_id', 'from_stop_order', 'to_stop_order', 'from_stop_name', 'to_stop_name', 'distance_km', 'duration_minutes', 'price', 'from_latitude', 'from_longitude', 'to_latitude', 'to_longitude').order_by('from_stop_order')
                )

            # Optimized trips queryset
            trips_qs = (
                Trip.objects.filter(driver=user)
                .select_related('route', 'vehicle')
                .only(
                    'id', 'trip_id', 'trip_date', 'departure_time', 'created_at', 'updated_at', 'trip_status',
                    'total_seats', 'available_seats', 'base_fare', 'gender_preference', 'notes', 'is_negotiable',
                    'total_distance_km', 'total_duration_minutes',
                    'route__route_id', 'route__route_name', 'route__route_description', 'route__total_distance_km', 'route__estimated_duration_minutes',
                    'vehicle__id', 'vehicle__model_number', 'vehicle__company_name', 'vehicle__plate_number', 'vehicle__vehicle_type', 'vehicle__color', 'vehicle__seats', 'vehicle__fuel_type',
                )
                .annotate(booking_count=Count('trip_bookings', filter=Q(trip_bookings__booking_status__in=['CONFIRMED', 'COMPLETED'])))
                .order_by('-created_at')
            )
            if not is_summary:
                trips_qs = trips_qs.prefetch_related(route_stops_prefetch, stop_breakdowns_prefetch)

            trips_qs = trips_qs[offset:offset + limit]

            rides_list = []
            for trip in trips_qs:
                route = trip.route
                route_names = []
                if not is_summary and route:
                    route_stops = list(route.route_stops.all())
                    route_names = [stop.stop_name for stop in route_stops] if route_stops else []
                else:
                    # In summary mode, try to derive names from fare_calculation if present, else leave empty
                    try:
                        if trip.fare_calculation and isinstance(trip.fare_calculation, dict):
                            sb = trip.fare_calculation.get('stop_breakdown') or []
                            if isinstance(sb, list) and sb:
                                first = sb[0]
                                last = sb[-1]
                                route_names = [str(first.get('from_stop_name') or 'From'), str(last.get('to_stop_name') or 'To')]
                    except Exception:
                        route_names = route_names or []

                # Vehicle details (from selected fields)
                vehicle = trip.vehicle
                vehicle_data = None
                if vehicle:
                    vehicle_data = {
                        'id': vehicle.id,
                        'model_number': vehicle.model_number,
                        'company_name': vehicle.company_name,
                        'plate_number': vehicle.plate_number,
                        'vehicle_type': vehicle.vehicle_type,
                        'color': vehicle.color,
                        'seats': vehicle.seats,
                        'fuel_type': vehicle.fuel_type,
                    }

                # Route coordinates (heavy) only in detail mode
                route_coordinates = []
                if not is_summary and route:
                    for stop in route.route_stops.all():
                        if stop.latitude and stop.longitude:
                            route_coordinates.append({'lat': float(stop.latitude), 'lng': float(stop.longitude), 'name': stop.stop_name, 'order': stop.stop_order})

                # Stop breakdowns (heavy) only in detail mode
                stop_breakdown = []
                if not is_summary:
                    for breakdown in trip.stop_breakdowns.all():
                        stop_breakdown.append({
                            'from_stop_name': breakdown.from_stop_name,
                            'to_stop_name': breakdown.to_stop_name,
                            'distance': float(breakdown.distance_km) if breakdown.distance_km is not None else None,
                            'duration': breakdown.duration_minutes,
                            'price': int(breakdown.price) if breakdown.price is not None else None,
                            'from_coordinates': {
                                'lat': float(breakdown.from_latitude) if breakdown.from_latitude is not None else None,
                                'lng': float(breakdown.from_longitude) if breakdown.from_longitude is not None else None,
                            },
                            'to_coordinates': {
                                'lat': float(breakdown.to_latitude) if breakdown.to_latitude is not None else None,
                                'lng': float(breakdown.to_longitude) if breakdown.to_longitude is not None else None,
                            },
                        })

                booking_count = getattr(trip, 'booking_count', 0) or 0

                ride_data = {
                    'id': trip.id,
                    'trip_id': trip.trip_id,
                    'trip_date': trip.trip_date.isoformat() if trip.trip_date else None,
                    'date': trip.trip_date.isoformat() if trip.trip_date else None,
                    'departure_time': trip.departure_time.strftime('%H:%M') if trip.departure_time else None,
                    'from_location': route_names[0] if route_names else 'Unknown',
                    'to_location': route_names[-1] if route_names else 'Unknown',
                    'route_names': route_names,
                    **({'route_coordinates': route_coordinates} if not is_summary else {}),
                    'distance': float(trip.total_distance_km) if trip.total_distance_km is not None else None,
                    'duration': trip.total_duration_minutes,
                    'custom_price': int(trip.base_fare) if trip.base_fare is not None else None,
                    'fare_collected': (int(trip.base_fare) if trip.base_fare is not None else 0) * booking_count,
                    'passenger_count': booking_count,
                    'vehicle_type': vehicle_data['vehicle_type'] if vehicle_data else 'Car',
                    'total_seats': trip.total_seats,
                    'available_seats': trip.available_seats,
                    'booking_count': booking_count,
                    'gender_preference': trip.gender_preference,
                    'description': trip.notes if trip.notes else '',
                    'status': map_trip_status_to_frontend(trip.trip_status),
                    'is_negotiable': trip.is_negotiable,
                    'created_at': trip.created_at.isoformat() if trip.created_at else None,
                    'updated_at': trip.updated_at.isoformat() if trip.updated_at else None,
                    'vehicle': vehicle_data,
                    **({
                        'route': {
                            'id': route.route_id if route else 'Unknown',
                            'name': route.route_name if route else 'Custom Route',
                            'description': route.route_description if route else 'Route description not available',
                            'total_distance_km': float(route.total_distance_km) if route and route.total_distance_km else 0.0,
                            'estimated_duration_minutes': int(route.estimated_duration_minutes) if route and route.estimated_duration_minutes else 0,
                            'route_stops': [
                                {
                                    'id': stop.id,
                                    'stop_order': stop.stop_order,
                                    'stop_name': stop.stop_name,
                                    'latitude': float(stop.latitude) if stop.latitude else 0.0,
                                    'longitude': float(stop.longitude) if stop.longitude else 0.0,
                                    'address': stop.address if stop.address else 'No address',
                                    'estimated_time_from_start': int(stop.estimated_time_from_start) if stop.estimated_time_from_start else 0,
                                } for stop in route_stops
                            ] if route_stops else []
                        },
                        'fare_calculation': trip.fare_calculation,
                        'stop_breakdown': stop_breakdown,
                    } if not is_summary else {}),
                    'can_edit': can_edit_trip(trip),
                    'can_delete': can_delete_trip(trip),
                    'can_cancel': can_cancel_trip(trip),
                }

                rides_list.append(ride_data)

            return JsonResponse({'success': True, 'rides': rides_list, 'total_rides': len(rides_list)})
        
        except UsersData.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'User not found'}, status=404)
        except Exception as e:
            import traceback
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)

@csrf_exempt
def get_trip_details(request, trip_id):
    """Get detailed information about a specific trip"""
    if request.method == 'GET':
        try:
            print('[GET_TRIP_DETAILS] START', trip_id)
            trip = Trip.objects.get(trip_id=trip_id)
            
            # Update status automatically
            trip = update_trip_status_automatically(trip)
            
            # Build route details safely
            route = getattr(trip, 'route', None)
            route_stops = []
            if route is not None:
                try:
                    route_stops = route.route_stops.all().order_by('stop_order')
                except Exception as _e:
                    print('[GET_TRIP_DETAILS] route_stops error:', _e)
                    route_stops = []
            
            # Get bookings
            bookings = trip.trip_bookings.filter(booking_status='CONFIRMED')
            booking_details = []
            for booking in bookings:
                booking_details.append({
                    'booking_id': booking.booking_id,
                    'passenger_name': booking.passenger.name,
                    'from_stop': booking.from_stop.stop_name,
                    'to_stop': booking.to_stop.stop_name,
                    'number_of_seats': booking.number_of_seats,
                    'total_fare': int(booking.total_fare) if booking.total_fare is not None else 0,
                    'booked_at': booking.booked_at.isoformat(),
                })
            
            # Get vehicle details
            vehicle_data = None
            if trip.vehicle:
                vehicle_data = {
                    'id': trip.vehicle.id,
                    'model_number': trip.vehicle.model_number,
                    'company_name': trip.vehicle.company_name,
                    'plate_number': trip.vehicle.plate_number,
                    'vehicle_type': trip.vehicle.vehicle_type,
                    'color': trip.vehicle.color,
                    'seats': trip.vehicle.seats,
                    'fuel_type': trip.vehicle.fuel_type,
                }
            
            # Build driver data safely
            driver = getattr(trip, 'driver', None)
            driver_data = None
            if driver is not None:
                driver_data = {
                    'id': driver.id,
                    'name': driver.name,
                    'phone_no': driver.phone_no,
                }

            # Serialize stop_breakdowns with coordinates from DB so frontend map can rebuild
            try:
                sb_qs = trip.stop_breakdowns.all().order_by('from_stop_order', 'to_stop_order')
            except Exception:
                sb_qs = []
            stop_breakdown = []
            for sb in sb_qs:
                stop_breakdown.append({
                    'from_stop_order': sb.from_stop_order,
                    'to_stop_order': sb.to_stop_order,
                    'from_stop_name': sb.from_stop_name,
                    'to_stop_name': sb.to_stop_name,
                    'distance_km': float(sb.distance_km) if sb.distance_km is not None else None,
                    'duration_minutes': sb.duration_minutes,
                    'price': int(sb.price) if sb.price is not None else None,
                    'from_coordinates': {
                        'lat': float(sb.from_latitude) if sb.from_latitude is not None else None,
                        'lng': float(sb.from_longitude) if sb.from_longitude is not None else None,
                    },
                    'to_coordinates': {
                        'lat': float(sb.to_latitude) if sb.to_latitude is not None else None,
                        'lng': float(sb.to_longitude) if sb.to_longitude is not None else None,
                    },
                    'price_breakdown': sb.price_breakdown or {},
                })

            # Build trip_data
            trip_data = {
                'trip_id': trip.trip_id,
                'trip_date': trip.trip_date.isoformat(),
                'departure_time': trip.departure_time.strftime('%H:%M'),
                'estimated_arrival_time': trip.estimated_arrival_time.strftime('%H:%M') if trip.estimated_arrival_time else None,
                'actual_departure_time': trip.actual_departure_time.strftime('%H:%M') if trip.actual_departure_time else None,
                'actual_arrival_time': trip.actual_arrival_time.strftime('%H:%M') if trip.actual_arrival_time else None,
                
                'status': map_trip_status_to_frontend(trip.trip_status),
                'total_seats': trip.total_seats,
                'available_seats': trip.available_seats,
                'base_fare': int(trip.base_fare) if trip.base_fare is not None else 0,
                
                'total_distance_km': float(trip.total_distance_km) if trip.total_distance_km else None,
                'total_duration_minutes': trip.total_duration_minutes,
                'fare_calculation': trip.fare_calculation,
                'stop_breakdown': stop_breakdown,
                
                'notes': trip.notes,
                'cancellation_reason': trip.cancellation_reason,
                
                'created_at': trip.created_at.isoformat(),
                'updated_at': trip.updated_at.isoformat(),
                'started_at': trip.started_at.isoformat() if trip.started_at else None,
                'completed_at': trip.completed_at.isoformat() if trip.completed_at else None,
                'cancelled_at': trip.cancelled_at.isoformat() if trip.cancelled_at else None,
                
                'vehicle': vehicle_data,
                'driver': driver_data,
                'route': None if route is None else {
                    'id': route.route_id,
                    'name': route.route_name,
                    'description': route.route_description,
                    'total_distance_km': float(route.total_distance_km) if route.total_distance_km else None,
                    'estimated_duration_minutes': route.estimated_duration_minutes,
                    'stops': [
                        {
                            'name': stop.stop_name,
                            'order': stop.stop_order,
                            'latitude': float(stop.latitude) if stop.latitude else None,
                            'longitude': float(stop.longitude) if stop.longitude else None,
                            'address': stop.address,
                            'estimated_time_from_start': stop.estimated_time_from_start,
                        }
                        for stop in route_stops
                    ],
                },
                'bookings': booking_details,
                'booking_count': len(booking_details),
                
                # Permissions
                'can_edit': can_edit_trip(trip),
                'can_delete': can_delete_trip(trip),
                'can_cancel': can_cancel_trip(trip),
            }
            
            print('[GET_TRIP_DETAILS] OK', trip_id, 'stops:', len(stop_breakdown))
            return JsonResponse({
                'success': True,
                'trip': trip_data,
            })
            
        except Trip.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)
        except Exception as e:
            print('[GET_TRIP_DETAILS] ERROR', trip_id, e)
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)
'''
def _fetch_route_geometry_osm(points):
    """Fetch dense road-following geometry from an OpenStreetMap-based directions API.
    points: list of (lat, lng) tuples.
    Returns a list of {"lat": float, "lng": float} along the road, or [] on failure.
    """
    try:
        print("[ROUTE_GEOMETRY][OSM] points:", points)
        if not points or len(points) < 2:
            print("[ROUTE_GEOMETRY][OSM] not enough points")
            return []
        # OpenRouteService-style API expects [lng, lat]
        coords = [[float(lng), float(lat)] for (lat, lng) in points]
        print("[ROUTE_GEOMETRY][OSM] coords for API:", coords)
        if not api_key:
            print("[ROUTE_GEOMETRY][OSM] missing api_key")
            return []
        url = "https://api.openrouteservice.org/v2/directions/driving-car"
        headers = {
            "Authorization": api_key,
            "Content-Type": "application/json",
        }
        # Request directions; newer ORS versions may return encoded polyline by default
        body = {
            "coordinates": coords,
            "instructions": False,
            "geometry_simplify": False,
        }
        print("[ROUTE_GEOMETRY][OSM] POST", url)
        resp = requests.post(url, json=body, headers=headers, timeout=15)
        print("[ROUTE_GEOMETRY][OSM] status", resp.status_code)
        print("[ROUTE_GEOMETRY][OSM] body", resp.text[:400])
        resp.raise_for_status()
        data = resp.json()

        # ORS v2 directions: geometry is under routes[0]["geometry"]
        routes = data.get('routes') or []
        if not routes:
            print('[ROUTE_GEOMETRY][OSM] no routes in response')
            return []

        geom = routes[0].get('geometry')

        # Case 1: GeoJSON LineString (some ORS configs / older versions)
        if isinstance(geom, dict) and geom.get('type') == 'LineString':
            line = []
            for lng, lat in geom.get('coordinates', []):
                try:
                    line.append({
                        'lat': float(lat),
                        'lng': float(lng),
                    })
                except Exception:
                    continue
            print('[ROUTE_GEOMETRY][OSM] extracted points from GeoJSON:', len(line))
            return line

        # Case 2: encoded polyline string (default in newer ORS versions)
        if isinstance(geom, str):
            decoded = _decode_ors_polyline(geom)
            line = []
            for lat, lng in decoded:
                try:
                    line.append({
                        'lat': float(lat),
                        'lng': float(lng),
                    })
                except Exception:
                    continue
            print('[ROUTE_GEOMETRY][OSM] extracted points from encoded polyline:', len(line))
            return line

        print('[ROUTE_GEOMETRY][OSM] unexpected geometry format:', type(geom), geom)
        return []
    except Exception as e:
        print('[ROUTE_GEOMETRY][OSM] failed to fetch geometry:', e)
        return []

'''
@csrf_exempt
def update_trip(request, trip_id):
    """Update trip details"""
    if request.method == 'PUT':
        try:
            data = json.loads(request.body.decode('utf-8'))
            try:
                print('=== UPDATE_TRIP DEBUG START ===')
                print(f"Trip ID: {trip_id}")
                print(f"Incoming keys: {list(data.keys())}")
                if 'fare_calculation' in data and isinstance(data.get('fare_calculation'), dict):
                    print(f"Fare calc keys: {list(data['fare_calculation'].keys())}")
                if 'stop_breakdown' in data and isinstance(data.get('stop_breakdown'), list):
                    print(f"Stop breakdown count: {len(data['stop_breakdown'])}")
                    if len(data['stop_breakdown']) > 0:
                        first = data['stop_breakdown'][0]
                        print(f"First stop raw keys: {list(first.keys())}")
                print('=== UPDATE_TRIP DEBUG END HEADER ===')
            except Exception as _e:
                print('UPDATE_TRIP DEBUG header logging failed:', _e)
            
            trip = Trip.objects.get(trip_id=trip_id)
            
            # Check if trip can be edited
            if not can_edit_trip(trip):
                return JsonResponse({
                    'success': False, 
                    'error': 'Trip cannot be edited. It may be completed, in progress, or have bookings.'
                }, status=400)
            
            # Update allowed fields
            if 'trip_date' in data:
                trip.trip_date = datetime.strptime(data['trip_date'], '%Y-%m-%d').date()
            
            if 'departure_time' in data:
                dep_hour, dep_minute = map(int, data['departure_time'].split(':'))
                trip.departure_time = time(dep_hour, dep_minute)
            
            if 'total_seats' in data:
                trip.total_seats = data['total_seats']
                trip.available_seats = data['total_seats']  # Reset available seats
            
            if 'base_fare' in data:
                trip.base_fare = _to_int_pkr(data.get('base_fare'), default=trip.base_fare)
            
            if 'gender_preference' in data:
                trip.gender_preference = data['gender_preference']
            
            if 'notes' in data:
                trip.notes = data['notes']
            
            if 'is_negotiable' in data:
                trip.is_negotiable = data['is_negotiable']
                print(f'DEBUG: Backend update_trip - Setting is_negotiable to: {data["is_negotiable"]}')
            
            if 'fare_calculation' in data:
                trip.fare_calculation = data['fare_calculation']
                trip.total_distance_km = data['fare_calculation'].get('total_distance_km')
                trip.total_duration_minutes = data['fare_calculation'].get('total_duration_minutes')
            
            # Update stop breakdowns if provided
            if 'stop_breakdown' in data:
                # Upsert breakdowns to avoid duplicate-key errors if client retries
                new_keys = set()

                # Create / update breakdowns
                for idx, stop_data in enumerate(data['stop_breakdown'] or []):
                    try:
                        # Coalesce legacy and new keys
                        from_order = stop_data.get('from_stop') if stop_data.get('from_stop') is not None else stop_data.get('from_stop_order')
                        to_order = stop_data.get('to_stop') if stop_data.get('to_stop') is not None else stop_data.get('to_stop_order')
                        distance = stop_data.get('distance') if stop_data.get('distance') is not None else stop_data.get('distance_km')
                        duration = stop_data.get('duration') if stop_data.get('duration') is not None else stop_data.get('duration_minutes')
                        # Final fallbacks
                        if duration is None:
                            duration = 0
                        if distance is None:
                            distance = 0.0

                        key = (from_order, to_order)
                        new_keys.add(key)

                        print(f"[UPDATE_TRIP][SB#{idx+1}] from={from_order} to={to_order} km={distance} min={duration} price={stop_data.get('price')}")
                        print(f"[UPDATE_TRIP][SB#{idx+1}] names=({stop_data.get('from_stop_name')} -> {stop_data.get('to_stop_name')})")

                        TripStopBreakdown.objects.update_or_create(
                            trip=trip,
                            from_stop_order=from_order,
                            to_stop_order=to_order,
                            defaults={
                                'from_stop_name': stop_data.get('from_stop_name'),
                                'to_stop_name': stop_data.get('to_stop_name'),
                                'distance_km': distance,
                                'duration_minutes': duration,
                                'price': _to_int_pkr(stop_data.get('price'), default=0),
                                'from_latitude': (stop_data.get('from_coordinates') or {}).get('lat'),
                                'from_longitude': (stop_data.get('from_coordinates') or {}).get('lng'),
                                'to_latitude': (stop_data.get('to_coordinates') or {}).get('lat'),
                                'to_longitude': (stop_data.get('to_coordinates') or {}).get('lng'),
                                'price_breakdown': stop_data.get('price_breakdown', {}),
                            },
                        )
                    except Exception as _ex:
                        print(f"[UPDATE_TRIP][SB#{idx+1}] ERROR while creating breakdown:", _ex)
                        raise

                # Remove any old breakdowns that are no longer present in the payload
                try:
                    existing = list(trip.stop_breakdowns.all())
                    for b in existing:
                        if (b.from_stop_order, b.to_stop_order) not in new_keys:
                            b.delete()
                except Exception as _cleanup_ex:
                    print('[UPDATE_TRIP][SB] cleanup error while removing stale breakdowns:', _cleanup_ex)
            
            # Safety: ensure gender_preference is never null to satisfy NOT NULL constraint
            try:
                if not getattr(trip, 'gender_preference', None):
                    trip.gender_preference = 'Any'
            except Exception:
                trip.gender_preference = 'Any'
            
            # Option A: keep Route / RouteStop geometry in sync with edited trip
            try:
                route = getattr(trip, 'route', None)
                route_coords = data.get('route_coordinates') or data.get('route_stops')
                if route and isinstance(route_coords, list) and len(route_coords) >= 2:
                    # Normalize to a simple list of dicts with name/order/lat/lng
                    normalized_stops = []
                    for idx, raw in enumerate(route_coords):
                        stop = raw or {}
                        lat = stop.get('lat')
                        lng = stop.get('lng')
                        # Some payloads may use latitude/longitude keys
                        if lat is None:
                            lat = stop.get('latitude')
                        if lng is None:
                            lng = stop.get('longitude')
                        name = stop.get('name') or stop.get('stop_name') or f"Stop {idx+1}"
                        order = stop.get('order') or (idx + 1)
                        normalized_stops.append({
                            'order': int(order),
                            'name': str(name),
                            'lat': lat,
                            'lng': lng,
                        })

                    # Sort by order to be safe
                    normalized_stops.sort(key=lambda s: s['order'])

                    # Update route name/description based on first and last stop names
                    try:
                        if normalized_stops:
                            origin_name = normalized_stops[0]['name']
                            destination_name = normalized_stops[-1]['name']
                            route.route_name = f"{origin_name} to {destination_name}"
                            route.route_description = f"Route from {origin_name} to {destination_name}"
                    except Exception as _name_ex:
                        print('[UPDATE_TRIP][ROUTE] failed to update name/description:', _name_ex)

                    # Replace existing RouteStop entries for this route
                    route.route_stops.all().delete()
                    for s in normalized_stops:
                        try:
                            RouteStop.objects.create(
                                route=route,
                                stop_name=s['name'],
                                stop_order=s['order'],
                                latitude=s['lat'],
                                longitude=s['lng'],
                            )
                        except Exception as _rs_ex:
                            print('[UPDATE_TRIP][ROUTE_STOP] error while creating stop', s, _rs_ex)

                    # Optionally refresh aggregate distance/duration if provided
                    fc = data.get('fare_calculation') or trip.fare_calculation or {}
                    try:
                        total_km = fc.get('total_distance_km') or fc.get('calculation_breakdown', {}).get('total_distance_km')
                        total_min = fc.get('total_duration_minutes') or fc.get('calculation_breakdown', {}).get('total_duration_minutes')
                        if total_km is not None:
                            route.total_distance_km = Decimal(str(total_km))
                        if total_min is not None:
                            route.estimated_duration_minutes = int(total_min)
                    except Exception as _agg_ex:
                        print('[UPDATE_TRIP][ROUTE] failed to update aggregates:', _agg_ex)

                    # Fetch and store dense road-following geometry using shared utility
                    update_route_geometry_from_stops(route, normalized_stops)
            except Exception as _route_ex:
                print('[UPDATE_TRIP][ROUTE_SYNC] error while syncing route geometry:', _route_ex)

            trip.save()
            
            return JsonResponse({
                'success': True,
                'message': 'Trip updated successfully',
                'trip_id': trip.trip_id,
            })
            
        except Trip.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)

@csrf_exempt
def delete_trip(request, trip_id):
    """Delete a trip"""
    if request.method == 'DELETE':
        try:
            trip = Trip.objects.get(trip_id=trip_id)
            
            # Check if trip can be deleted
            if not can_delete_trip(trip):
                return JsonResponse({
                    'success': False, 
                    'error': 'Trip cannot be deleted. It may be completed, in progress, or have bookings.'
                }, status=400)
            
            # Delete the trip
            trip.delete()
            
            return JsonResponse({
                'success': True,
                'message': 'Trip deleted successfully',
            })
            
        except Trip.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)

@csrf_exempt
def cancel_trip(request, trip_id):
    """Cancel a trip"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body.decode('utf-8'))
            reason = data.get('reason', 'Cancelled by driver')
            
            trip = Trip.objects.get(trip_id=trip_id)
            
            # Check if trip can be cancelled
            if not can_cancel_trip(trip):
                return JsonResponse({
                    'success': False, 
                    'error': 'Trip cannot be cancelled. It may already be cancelled or completed.'
                }, status=400)
            
            # Cancel the trip
            trip.trip_status = 'CANCELLED'
            trip.cancellation_reason = reason
            trip.cancelled_at = timezone.now()
            trip.save()

            # Cancel all remaining bookings (including in-progress passengers).
            active_bookings = trip.trip_bookings.exclude(booking_status__in=['CANCELLED', 'COMPLETED'])
            now = timezone.now()
            for booking in active_bookings:
                booking.booking_status = 'CANCELLED'
                booking.cancelled_at = now
                # If passenger was on board, reflect cancellation explicitly.
                if getattr(booking, 'ride_status', None) == 'RIDE_STARTED':
                    booking.ride_status = 'CANCELLED_ON_BOARD'
                    booking.save(update_fields=['booking_status', 'cancelled_at', 'ride_status', 'updated_at'])
                else:
                    booking.save(update_fields=['booking_status', 'cancelled_at', 'updated_at'])

                # Notify each passenger that the trip was cancelled by the driver
                try:
                    passenger = booking.passenger
                    if passenger and getattr(passenger, 'id', None):
                        payload = {
                            'user_id': str(passenger.id),
                            'driver_id': str(trip.driver.id) if trip.driver_id else None,
                            'title': 'Ride cancelled by driver',
                            'body': f'Your LetsGo ride {trip.trip_id} was cancelled by the driver. '
                                    'Please search for another ride.',
                            'data': {
                                'type': 'trip_cancelled_by_driver',
                                'trip_id': str(trip.trip_id),
                                'booking_id': str(booking.id),
                            },
                        }
                        send_ride_notification_async(payload)
                except Exception as e:
                    print('[cancel_trip][notify_passenger][ERROR]:', e)

            return JsonResponse({
                'success': True,
                'message': 'Trip cancelled successfully',
                'cancelled_bookings_count': active_bookings.count(),
            })
            
        except Trip.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)

# Additional view functions for API compatibility
@csrf_exempt
def get_route_details(request, route_id):
    """Get route details"""
    if request.method == 'GET':
        try:
            route = Route.objects.get(id=route_id)
            route_data = {
                'id': route.route_id,
                'name': route.route_name,
                'description': route.route_description,
                'total_distance_km': float(route.total_distance_km) if route.total_distance_km else None,
                'estimated_duration_minutes': route.estimated_duration_minutes,
                'stops': [
                    {
                        'name': stop.stop_name,
                        'order': stop.stop_order,
                        'latitude': float(stop.latitude) if stop.latitude else None,
                        'longitude': float(stop.longitude) if stop.longitude else None,
                        'address': stop.address,
                        'estimated_time_from_start': stop.estimated_time_from_start,
                    }
                    for stop in route.route_stops.all().order_by('stop_order')
                ],
            }
            return JsonResponse({'success': True, 'route': route_data})
        except Route.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Route not found'}, status=404)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)

@csrf_exempt
def get_route_statistics(request, route_id):
    """Get route statistics"""
    if request.method == 'GET':
        try:
            route = Route.objects.get(id=route_id)
            trips = Trip.objects.filter(route=route)
            
            statistics = {
                'total_trips': trips.count(),
                'completed_trips': trips.filter(trip_status='COMPLETED').count(),
                'cancelled_trips': trips.filter(trip_status='CANCELLED').count(),
                'total_bookings': sum(trip.trip_bookings.count() for trip in trips),
                'total_revenue': int(sum(int(trip.base_fare or 0) for trip in trips)),
            }
            return JsonResponse({'success': True, 'statistics': statistics})
        except Route.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Route not found'}, status=404)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)

@csrf_exempt
def search_routes(request):
    """Search routes"""
    if request.method == 'GET':
        try:
            from_location = request.GET.get('from')
            to_location = request.GET.get('to')
            date = request.GET.get('date')
            min_seats = request.GET.get('min_seats')
            max_price = request.GET.get('max_price')
            
            routes = Route.objects.filter(is_active=True)
            
            # Apply filters
            if from_location:
                routes = routes.filter(route_stops__stop_name__icontains=from_location)
            if to_location:
                routes = routes.filter(route_stops__stop_name__icontains=to_location)
            
            routes_data = []
            for route in routes.distinct():
                routes_data.append({
                    'id': route.route_id,
                    'name': route.route_name,
                    'description': route.route_description,
                    'total_distance_km': float(route.total_distance_km) if route.total_distance_km else None,
                    'estimated_duration_minutes': route.estimated_duration_minutes,
                })
            
            return JsonResponse({'success': True, 'routes': routes_data})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)

@csrf_exempt
def get_available_seats(request, trip_id):
    """Get available seats for a trip"""
    if request.method == 'GET':
        try:
            trip = Trip.objects.get(id=trip_id)
            booked_seats = []
            
            # Get booked seats
            for booking in trip.trip_bookings.filter(booking_status='CONFIRMED'):
                booked_seats.extend(booking.seat_numbers)
            
            # Generate available seats
            all_seats = list(range(1, trip.total_seats + 1))
            available_seats = [seat for seat in all_seats if seat not in booked_seats]
            
            return JsonResponse({
                'success': True,
                'available_seats': available_seats,
                'total_seats': trip.total_seats,
                'booked_seats': booked_seats,
            })
        except Trip.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)

@csrf_exempt
def create_booking(request):
    """Create a booking"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body.decode('utf-8'))
            
            # This is a placeholder - implement actual booking logic
            booking_data = {
                'booking_id': f"B{random.randint(100, 999)}-{datetime.now().strftime('%Y-%m-%d-%H%M')}",
                'success': True,
                'message': 'Booking created successfully',
            }
            
            return JsonResponse(booking_data)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)

@csrf_exempt
def get_user_bookings(request, user_id):
    """Get user's bookings"""
    if request.method == 'GET':
        try:
            print(f"DEBUG: get_user_bookings called for user_id: {user_id}")
            # Summary mode for lightweight list
            mode = (request.GET.get('mode') or '').lower()
            is_summary = mode == 'summary'

            # Fetch user minimally to avoid heavy column loads
            user = UsersData.objects.only('id', 'name').get(id=user_id)
            print(f"DEBUG: User found: {user.name}")
            
            # Pagination to avoid huge result sets
            try:
                limit = int(request.GET.get('limit', 20))
                limit = max(1, min(limit, 200))  # cap between 1 and 200
            except Exception:
                limit = 20
            try:
                offset = int(request.GET.get('offset', 0))
                offset = max(0, offset)
            except Exception:
                offset = 0

            # Prefetch only route stops when not in summary mode
            route_stops_prefetch = None
            if not is_summary:
                route_stops_prefetch = Prefetch(
                    'trip__route__route_stops',
                    queryset=RouteStop.objects.only(
                        'id', 'stop_order', 'stop_name', 'latitude', 'longitude', 'address', 'estimated_time_from_start'
                    ).order_by('stop_order')
                )

            # Build optimized queryset: select only needed fields, avoid heavy BinaryFields via related models
            bookings_queryset = (
                Booking.objects.filter(passenger=user)
                .select_related(
                    'trip',
                    'trip__driver',
                    'trip__vehicle',
                    'trip__route',
                    'from_stop',
                    'to_stop',
                )
                .only(
                    # Booking fields
                    'booking_id', 'id', 'booking_status', 'payment_status', 'bargaining_status',
                    'number_of_seats', 'male_seats', 'female_seats', 'seat_numbers', 'total_fare', 'original_fare', 'negotiated_fare',
                    'passenger_offer', 'driver_response', 'negotiation_notes', 'fare_breakdown',
                    'passenger_rating', 'passenger_feedback', 'booked_at', 'cancelled_at', 'completed_at', 'updated_at',
                    # Trip fields
                    'trip__trip_id', 'trip__trip_date', 'trip__departure_time', 'trip__estimated_arrival_time',
                    'trip__trip_status', 'trip__total_seats', 'trip__available_seats', 'trip__base_fare',
                    'trip__gender_preference', 'trip__notes', 'trip__is_negotiable',
                    # Driver fields (avoid binary fields)
                    'trip__driver__id', 'trip__driver__name', 'trip__driver__phone_no',
                    'trip__driver__driver_rating', 'trip__driver__gender',
                    # Vehicle fields
                    'trip__vehicle__id', 'trip__vehicle__company_name', 'trip__vehicle__model_number',
                    'trip__vehicle__plate_number', 'trip__vehicle__color', 'trip__vehicle__vehicle_type',
                    'trip__vehicle__seats',
                    # Route fields
                    'trip__route__route_id', 'trip__route__route_name', 'trip__route__route_description',
                    'trip__route__total_distance_km', 'trip__route__estimated_duration_minutes',
                    # From/To stop fields
                    'from_stop__stop_name', 'from_stop__stop_order', 'from_stop__latitude', 'from_stop__longitude',
                    'to_stop__stop_name', 'to_stop__stop_order', 'to_stop__latitude', 'to_stop__longitude',
                )
                .prefetch_related(*( [route_stops_prefetch] if route_stops_prefetch is not None else [] ))
                .order_by('-booked_at')
            )

            # Apply slicing for pagination on the queryset
            bookings_queryset = bookings_queryset[offset:offset + limit]
            
            bookings = []
            for booking in bookings_queryset:
                try:
                    print(f"DEBUG: Processing booking {booking.booking_id}")
                    
                    # Get trip data
                    trip = booking.trip
                    driver = trip.driver
                    vehicle = trip.vehicle
                    route = trip.route
                    
                    # Get route names; in summary mode, avoid loading stops
                    if not is_summary and route:
                        route_stops = route.route_stops.all().order_by('stop_order')
                        route_names = [stop.stop_name for stop in route_stops] if route_stops else ['Unknown']
                    else:
                        route_names = [booking.from_stop.stop_name if booking.from_stop else 'From', booking.to_stop.stop_name if booking.to_stop else 'To']

                    if is_summary:
                        booking_data = {
                            'booking_id': booking.booking_id,
                            'id': booking.id,
                            'db_id': booking.id,
                            'route_names': route_names,
                            'trip_id': trip.trip_id,
                            'trip_date': trip.trip_date.isoformat() if trip.trip_date else None,
                            'departure_time': trip.departure_time.strftime('%H:%M') if trip.departure_time else None,
                            'distance': float(route.total_distance_km) if route and route.total_distance_km else None,
                            'total_seats': trip.total_seats,
                            'available_seats': trip.available_seats,
                            'status': booking.booking_status,
                            'booking_status': booking.booking_status,
                            'payment_status': booking.payment_status,
                            'total_fare': int(booking.total_fare) if booking.total_fare is not None else 0,
                            # Passenger segment orders for frontend map colouring
                            'from_stop_order': booking.from_stop.stop_order if booking.from_stop else None,
                            'to_stop_order': booking.to_stop.stop_order if booking.to_stop else None,
                            # Minimal negotiation fields so passenger screens can show latest state
                            'bargaining_status': booking.bargaining_status,
                            'negotiated_fare': int(booking.negotiated_fare) if booking.negotiated_fare is not None else None,
                            'passenger_offer': int(booking.passenger_offer) if booking.passenger_offer is not None else None,
                            'passenger_id': booking.passenger_id,
                            'driver_response': booking.driver_response,
                            'negotiation_notes': booking.negotiation_notes,
                            'vehicle': {
                                'model_number': vehicle.model_number if vehicle else None,
                                'company_name': vehicle.company_name if vehicle else None,
                                'plate_number': vehicle.plate_number if vehicle else None,
                                'seats': vehicle.seats if vehicle else None,
                                'vehicle_type': vehicle.vehicle_type if vehicle else None,
                            } if vehicle else None,
                        }
                    else:
                        booking_data = {
                            'booking_id': booking.booking_id,
                            'id': booking.id,  # Add numeric ID for API calls
                            'trip_id': trip.trip_id,
                            'status': booking.booking_status,
                            'booking_status': booking.booking_status,
                            'payment_status': booking.payment_status,
                            'bargaining_status': booking.bargaining_status,

                            # Frontend expected fields for passenger ride history
                            'from_location': booking.from_stop.stop_name if booking.from_stop else 'Unknown',
                            'to_location': booking.to_stop.stop_name if booking.to_stop else 'Unknown',
                            'date': trip.trip_date.isoformat() if trip.trip_date else None,
                            'fare': int(booking.total_fare) if booking.total_fare is not None else 0,

                            # Trip information
                            'trip': {
                                'trip_id': trip.trip_id,
                                'trip_date': trip.trip_date.isoformat() if trip.trip_date else None,
                                'departure_time': trip.departure_time.strftime('%H:%M') if trip.departure_time else None,
                                'arrival_time': trip.estimated_arrival_time.strftime('%H:%M') if trip.estimated_arrival_time else None,
                                'trip_status': trip.trip_status,
                                'total_seats': trip.total_seats,
                                'available_seats': trip.available_seats,
                                'base_fare': int(trip.base_fare) if trip.base_fare is not None else 0,
                                'gender_preference': trip.gender_preference,
                                'notes': trip.notes,
                                'is_negotiable': trip.is_negotiable,

                                # Driver information
                                'driver': {
                                    'id': driver.id if driver else None,
                                    'name': driver.name if driver else 'Unknown Driver',
                                    'phone': driver.phone_no if driver else None,
                                    'driver_rating': float(driver.driver_rating) if driver and driver.driver_rating else 0.0,
                                    'gender': driver.gender if driver else None,
                                },

                                # Vehicle information
                                'vehicle': {
                                    'id': vehicle.id if vehicle else None,
                                    'make': vehicle.company_name if vehicle else 'Unknown',
                                    'model': vehicle.model_number if vehicle else 'Unknown',
                                    'license_plate': vehicle.plate_number if vehicle else 'Unknown',
                                    'color': vehicle.color if vehicle else 'Unknown',
                                    'vehicle_type': vehicle.vehicle_type if vehicle else 'Unknown',
                                    'seats': vehicle.seats if vehicle else 0,
                                },

                                # Route information with stops for map display
                                'route': {
                                    'id': route.route_id if route else 'Unknown',
                                    'name': route.route_name if route else 'Custom Route',
                                    'description': route.route_description if route else 'Route description not available',
                                    'total_distance_km': float(route.total_distance_km) if route and route.total_distance_km else 0.0,
                                    'estimated_duration_minutes': int(route.estimated_duration_minutes) if route and route.estimated_duration_minutes else 0,
                                    'route_stops': [
                                        {
                                            'id': stop.id,
                                            'stop_order': stop.stop_order,
                                            'stop_name': stop.stop_name,
                                            'latitude': float(stop.latitude) if stop.latitude else 0.0,
                                            'longitude': float(stop.longitude) if stop.longitude else 0.0,
                                            'address': stop.address if stop.address else 'No address',
                                            'estimated_time_from_start': int(stop.estimated_time_from_start) if stop.estimated_time_from_start else 0,
                                        } for stop in route_stops
                                    ] if route_stops else []
                                }
                            },

                            # Route information
                            'route_names': route_names,
                            'distance': float(route.total_distance_km) if route and route.total_distance_km else 0.0,
                            'custom_price': int(trip.base_fare) if trip.base_fare is not None else 0,

                            # Stop information
                            # Expose passenger segment orders at root as well for convenience
                            'from_stop_order': booking.from_stop.stop_order if booking.from_stop else None,
                            'to_stop_order': booking.to_stop.stop_order if booking.to_stop else None,
                            'from_stop': {
                                'stop_name': booking.from_stop.stop_name if booking.from_stop else 'Unknown',
                                'stop_order': booking.from_stop.stop_order if booking.from_stop else 0,
                                'latitude': float(booking.from_stop.latitude) if booking.from_stop and booking.from_stop.latitude else 0.0,
                                'longitude': float(booking.from_stop.longitude) if booking.from_stop and booking.from_stop.longitude else 0.0,
                            },
                            'to_stop': {
                                'stop_name': booking.to_stop.stop_name if booking.to_stop else 'Unknown',
                                'stop_order': booking.to_stop.stop_order if booking.to_stop else 0,
                                'latitude': float(booking.to_stop.latitude) if booking.to_stop and booking.to_stop.latitude else 0.0,
                                'longitude': float(booking.to_stop.longitude) if booking.to_stop and booking.to_stop.longitude else 0.0,
                            },

                            # Booking details
                            'number_of_seats': booking.number_of_seats,
                            'male_seats': int(getattr(booking, 'male_seats', 0) or 0),
                            'female_seats': int(getattr(booking, 'female_seats', 0) or 0),
                            'seat_numbers': booking.seat_numbers if booking.seat_numbers else [],
                            'total_fare': int(booking.total_fare) if booking.total_fare is not None else 0,
                            'original_fare': int(booking.original_fare) if booking.original_fare is not None else None,
                            'negotiated_fare': int(booking.negotiated_fare) if booking.negotiated_fare is not None else None,
                            'passenger_offer': int(booking.passenger_offer) if booking.passenger_offer is not None else None,
                            'driver_response': booking.driver_response,
                            'negotiation_notes': booking.negotiation_notes,
                            'fare_breakdown': booking.fare_breakdown if booking.fare_breakdown else {},

                            # Ratings and feedback
                            'passenger_rating': float(booking.passenger_rating) if booking.passenger_rating else None,
                            'passenger_feedback': booking.passenger_feedback,

                            # Timestamps
                            'booked_at': booking.booked_at.isoformat() if booking.booked_at else None,
                            'cancelled_at': booking.cancelled_at.isoformat() if booking.cancelled_at else None,
                            'completed_at': booking.completed_at.isoformat() if booking.completed_at else None,
                            'updated_at': booking.updated_at.isoformat() if booking.updated_at else None,
                        }
                    
                    bookings.append(booking_data)
                    print(f"DEBUG: Successfully processed booking {booking.booking_id}")
                    
                except Exception as e:
                    print(f"DEBUG: Error processing booking {booking.id}: {str(e)}")
                    import traceback
                    traceback.print_exc()
                    continue
            
            print(f"DEBUG: Returning {len(bookings)} bookings to frontend")
            return JsonResponse({'success': True, 'bookings': bookings})
            
        except UsersData.DoesNotExist:
            print(f"DEBUG: User with id {user_id} not found")
            return JsonResponse({'success': False, 'error': 'User not found'}, status=404)
        except Exception as e:
            print(f"DEBUG: Exception in get_user_bookings: {str(e)}")
            import traceback
            traceback.print_exc()
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)



@csrf_exempt
def search_rides(request):
    """Search rides"""
    if request.method == 'GET':
        try:
            from_location = request.GET.get('from')
            to_location = request.GET.get('to')
            date = request.GET.get('date')
            min_seats = request.GET.get('min_seats')
            max_price = request.GET.get('max_price')
            gender_preference = request.GET.get('gender_preference')
            
            trips = Trip.objects.filter(trip_status='SCHEDULED')
            
            # Apply filters
            if from_location:
                trips = trips.filter(route__route_stops__stop_name__icontains=from_location)
            if to_location:
                trips = trips.filter(route__route_stops__stop_name__icontains=to_location)
            if date:
                trips = trips.filter(trip_date=date)
            if min_seats:
                trips = trips.filter(available_seats__gte=int(min_seats))
            if max_price:
                try:
                    trips = trips.filter(base_fare__lte=int(round(float(max_price))))
                except (TypeError, ValueError):
                    pass
            
            rides_data = []
            for trip in trips.distinct():
                rides_data.append({
                    'trip_id': trip.trip_id,
                    'trip_date': trip.trip_date.isoformat(),
                    'departure_time': trip.departure_time.strftime('%H:%M'),
                    'origin': trip.route.first_stop.stop_name if trip.route.first_stop else trip.route.route_name,
                    'destination': trip.route.last_stop.stop_name if trip.route.last_stop else trip.route.route_name,
                    'driver_name': trip.driver.name,
                    'vehicle_model': f"{trip.vehicle.company_name} {trip.vehicle.model_number}" if trip.vehicle else 'Unknown Vehicle',
                    'available_seats': trip.available_seats,
                    'price_per_seat': int(trip.base_fare) if trip.base_fare is not None else 0,
                    'total_seats': trip.total_seats,
                })
            
            return JsonResponse({'success': True, 'rides': rides_data})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)

@csrf_exempt
def cancel_ride(request, ride_id):
    """Cancel a ride"""
    if request.method == 'DELETE':
        try:
            # This is a placeholder - implement actual ride cancellation
            return JsonResponse({'success': True, 'message': 'Ride cancelled successfully'})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=400)


