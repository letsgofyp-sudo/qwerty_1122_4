from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.conf import settings
import json
import math
from datetime import datetime, timedelta

from .models.models_trip import Trip, TripStopBreakdown, TripLiveLocationUpdate, RideAuditEvent
from .models.models_booking import Booking, PickupCodeVerification
from .models.models_route import RouteStop
from .models import TripPayment
from .views_authentication import upload_to_supabase
from .views_notifications import send_ride_notification_async
from .utils.verification_guard import verification_block_response


def _coerce_int(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    try:
        return int(str(v).strip())
    except Exception:
        return None


def _coerce_float(v):
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        try:
            return float(v)
        except Exception:
            return None
    try:
        return float(str(v).strip())
    except Exception:
        return None


def _parse_iso_dt(v):
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v))
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt
    except Exception:
        return None


def _get_trip_or_404(trip_id):
    """Fetch a Trip by trip_id or return a JSON 404 response."""
    try:
        trip = Trip.objects.get(trip_id=trip_id)
        return trip, None
    except Trip.DoesNotExist:
        return None, JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)


def _haversine_meters(lat1, lon1, lat2, lon2):
    try:
        lat1 = float(lat1)
        lon1 = float(lon1)
        lat2 = float(lat2)
        lon2 = float(lon2)
    except Exception:
        return None

    r = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return r * c


def _record_system_notification_if_due(trip: Trip, key: str, cooldown_seconds: int) -> bool:
    """Return True if notification is due and record timestamp in live_tracking_state."""
    try:
        now = timezone.now()
        state = trip.live_tracking_state
        if not isinstance(state, dict):
            state = {}
        sent = state.get('system_notifications')
        if not isinstance(sent, dict):
            sent = {}
        last_dt = _parse_iso_dt(sent.get(key))
        if last_dt is not None:
            try:
                if (now - last_dt).total_seconds() < float(cooldown_seconds):
                    return False
            except Exception:
                pass

        sent[key] = now.isoformat()
        state['system_notifications'] = sent
        trip.live_tracking_state = state
        trip.save(update_fields=['live_tracking_state'])
        return True
    except Exception:
        return True


def _set_trip_booking_flag(trip: Trip, booking_id: int, flag: str, value) -> None:
    try:
        state = trip.live_tracking_state
        if not isinstance(state, dict):
            state = {}
        flags = state.get('booking_flags')
        if not isinstance(flags, dict):
            flags = {}
        bid = str(int(booking_id))
        row = flags.get(bid)
        if not isinstance(row, dict):
            row = {}
        row[flag] = value
        flags[bid] = row
        state['booking_flags'] = flags
        trip.live_tracking_state = state
        trip.save(update_fields=['live_tracking_state'])
    except Exception:
        pass


@csrf_exempt
def get_ride_readiness(request, trip_id):
    """GET /rides/{trip_id}/readiness

    Aggregates readiness across all CONFIRMED bookings on a trip.

    Response:
    {
      "success": true,
      "trip_id": "T...",
      "ready_count": N,
      "total_confirmed": M,
      "list": [
        {"booking_id": ..., "passenger_id": ..., "status": "READY"|"NOT_READY"|"UNKNOWN"}
      ]
    }
    """
    if request.method != 'GET':
        return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=405)

    trip, error = _get_trip_or_404(trip_id)
    if error is not None:
        return error

    confirmed_qs = (
        Booking.objects
        .filter(trip=trip, booking_status='CONFIRMED')
        .select_related('passenger')
    )

    ready_count = 0
    readiness_list = []

    for booking in confirmed_qs:
        status = getattr(booking, 'readiness_status', None) or 'UNKNOWN'
        if status not in ['READY', 'NOT_READY', 'UNKNOWN']:
            status = 'UNKNOWN'
        if status == 'READY':
            ready_count += 1
        readiness_list.append({
            'booking_id': booking.id,
            'passenger_id': booking.passenger.id,
            'status': status,
        })

    return JsonResponse({
        'success': True,
        'trip_id': trip_id,
        'ready_count': ready_count,
        'total_confirmed': confirmed_qs.count(),
        'list': readiness_list,
    })


@csrf_exempt
def update_booking_readiness(request, booking_id):
    """POST endpoint for passengers/drivers to mark readiness for a booking.

    Body JSON: {"status": "READY" | "NOT_READY"}
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=405)

    import json

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    status = data.get('status')
    if status not in ['READY', 'NOT_READY']:
        return JsonResponse({
            'success': False,
            'error': 'Invalid readiness status. Must be READY or NOT_READY.',
        }, status=400)

    try:
        booking = Booking.objects.select_related('trip', 'passenger').get(id=booking_id)
    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)

    booking.readiness_status = status
    booking.updated_at = timezone.now()
    booking.save(update_fields=['readiness_status', 'updated_at'])

    return JsonResponse({
        'success': True,
        'booking_id': booking.id,
        'trip_id': booking.trip.trip_id,
        'passenger_id': booking.passenger.id,
        'status': status,
    })


@csrf_exempt
@require_http_methods(["POST"])
def start_trip_ride(request, trip_id):
    """Driver starts trip; marks trip IN_PROGRESS, records who/when, notifies passengers."""
    trip, error = _get_trip_or_404(trip_id)
    if error is not None:
        return error

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    driver_id = _coerce_int(data.get('driver_id'))
    if driver_id is None or trip.driver_id != driver_id:
        return JsonResponse({'success': False, 'error': 'Not authorized as driver for this trip'}, status=403)

    blocked = verification_block_response(driver_id)
    if blocked is not None:
        return blocked

    if trip.trip_status not in ['SCHEDULED', 'IN_PROGRESS']:
        return JsonResponse({'success': False, 'error': f'Cannot start trip in status {trip.trip_status}'}, status=400)

    now = timezone.now()
    if trip.trip_status != 'IN_PROGRESS':
        trip.trip_status = 'IN_PROGRESS'
        trip.actual_departure_time = now.time()
        trip.started_at = now
        trip.started_by_user_id = driver_id

    state = trip.live_tracking_state or {}
    state.setdefault('passengers', [])
    state['last_update'] = now.isoformat()
    trip.live_tracking_state = state
    trip.save(update_fields=['trip_status', 'actual_departure_time', 'started_at', 'started_by_user', 'live_tracking_state'])

    try:
        RideAuditEvent.objects.create(
            trip=trip,
            booking=None,
            actor=trip.driver,
            event_type='TRIP_STARTED',
            payload={
                'driver_id': driver_id,
                'started_at': now.isoformat(),
            },
        )
    except Exception:
        pass

    confirmed = (
        Booking.objects
        .filter(trip=trip, booking_status='CONFIRMED')
        .select_related('passenger', 'from_stop', 'to_stop')
    )
    for booking in confirmed:
        payload = {
            'user_id': str(booking.passenger.id),
            'driver_id': str(trip.driver_id),
            'title': 'Ride started',
            'body': f'Your ride {trip.trip_id} has started.',
            'data': {
                'type': 'ride_started',
                'trip_id': str(trip.trip_id),
                'booking_id': str(booking.id),
            },
        }
        try:
            send_ride_notification_async(payload)
        except Exception:
            pass

        try:
            passenger_name = getattr(getattr(booking, 'passenger', None), 'name', None) or 'Passenger'
            pickup_name = getattr(getattr(booking, 'from_stop', None), 'stop_name', None) or 'Pickup'
            drop_name = getattr(getattr(booking, 'to_stop', None), 'stop_name', None) or 'Drop-off'
            payload_driver = {
                'user_id': str(trip.driver_id),
                'driver_id': str(trip.driver_id),
                'title': 'Pickup passenger',
                'body': f'You have to pick {passenger_name} at {pickup_name}.',
                'data': {
                    'type': 'driver_task_pickup',
                    'trip_id': str(trip.trip_id),
                    'booking_id': str(booking.id),
                    'passenger_name': str(passenger_name),
                    'pickup_stop_name': str(pickup_name),
                    'dropoff_stop_name': str(drop_name),
                },
            }
            send_ride_notification_async(payload_driver)
        except Exception:
            pass

    return JsonResponse({'success': True, 'trip_id': trip.trip_id, 'trip_status': trip.trip_status})


@csrf_exempt
@require_http_methods(["POST"])
def start_booking_ride(request, booking_id):
    """Passenger confirms they started ride; records who/when and notifies driver."""
    try:
        booking = Booking.objects.select_related('trip', 'passenger').get(id=booking_id)
    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    passenger_id = _coerce_int(data.get('passenger_id'))
    if passenger_id is None or booking.passenger_id != passenger_id:
        return JsonResponse({'success': False, 'error': 'Not authorized as passenger for this booking'}, status=403)

    blocked = verification_block_response(passenger_id)
    if blocked is not None:
        return blocked

    if booking.booking_status != 'CONFIRMED':
        return JsonResponse({'success': False, 'error': f'Cannot start booking in status {booking.booking_status}'}, status=400)

    now = timezone.now()
    booking.started_at = now
    booking.started_by_passenger_id = passenger_id
    booking.ride_status = 'RIDE_STARTED'
    booking.updated_at = now
    booking.save(update_fields=['started_at', 'started_by_passenger', 'ride_status', 'updated_at'])

    try:
        RideAuditEvent.objects.create(
            trip=booking.trip,
            booking=booking,
            actor=booking.passenger,
            event_type='PASSENGER_MARKED_ON_BOARD',
            payload={
                'booking_id': booking.id,
                'passenger_id': passenger_id,
                'timestamp': now.isoformat(),
            },
        )
    except Exception:
        pass

    trip = booking.trip
    payload = {
        'user_id': str(trip.driver_id),
        'driver_id': str(trip.driver_id),
        'title': 'Passenger on board',
        'body': f'Passenger {booking.passenger_id} has started their ride.',
        'data': {
            'type': 'passenger_started',
            'trip_id': str(trip.trip_id),
            'booking_id': str(booking.id),
        },
    }
    try:
        send_ride_notification_async(payload)
    except Exception:
        pass

    return JsonResponse({'success': True, 'booking_id': booking.id, 'trip_id': trip.trip_id})


@csrf_exempt
@require_http_methods(["POST"])
def complete_trip_ride(request, trip_id):
    trip, error = _get_trip_or_404(trip_id)
    if error is not None:
        return error

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    driver_id = _coerce_int(data.get('driver_id'))
    if driver_id is None or trip.driver_id != driver_id:
        return JsonResponse({'success': False, 'error': 'Not authorized as driver for this trip'}, status=403)

    if trip.trip_status not in ['SCHEDULED', 'IN_PROGRESS']:
        return JsonResponse({'success': False, 'error': f'Cannot complete trip in status {trip.trip_status}'}, status=400)

    now = timezone.now()
    trip.trip_status = 'COMPLETED'
    trip.actual_arrival_time = now.time()
    trip.completed_at = now

    state = trip.live_tracking_state or {}
    state['ended_at'] = now.isoformat()
    state['ended_by_user_id'] = driver_id
    trip.live_tracking_state = state
    trip.save(update_fields=['trip_status', 'actual_arrival_time', 'completed_at', 'live_tracking_state'])

    try:
        RideAuditEvent.objects.create(
            trip=trip,
            booking=None,
            actor=trip.driver,
            event_type='TRIP_COMPLETED',
            payload={
                'driver_id': driver_id,
                'completed_at': now.isoformat(),
            },
        )
    except Exception:
        pass

    try:
        confirmed = Booking.objects.filter(trip=trip, booking_status__in=['CONFIRMED', 'COMPLETED']).select_related('passenger')
        for booking in confirmed:
            payload = {
                'user_id': str(booking.passenger.id),
                'driver_id': str(trip.driver_id),
                'title': 'Trip completed',
                'body': f'Your trip {trip.trip_id} has completed.',
                'data': {
                    'type': 'trip_completed',
                    'trip_id': str(trip.trip_id),
                    'booking_id': str(booking.id),
                },
            }
            try:
                send_ride_notification_async(payload)
            except Exception:
                pass
    except Exception:
        pass

    return JsonResponse({'success': True, 'trip_id': trip.trip_id, 'trip_status': trip.trip_status})


@csrf_exempt
@require_http_methods(["POST"])
def mark_booking_dropped_off(request, booking_id):
    try:
        booking = Booking.objects.select_related('trip', 'passenger').get(id=booking_id)
    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    passenger_id = _coerce_int(data.get('passenger_id'))
    if passenger_id is None or booking.passenger_id != passenger_id:
        return JsonResponse({'success': False, 'error': 'Not authorized as passenger for this booking'}, status=403)

    if booking.booking_status not in ['CONFIRMED', 'COMPLETED']:
        return JsonResponse({'success': False, 'error': f'Cannot drop off booking in status {booking.booking_status}'}, status=400)

    now = timezone.now()
    booking.booking_status = 'COMPLETED'
    booking.ride_status = 'DROPPED_OFF'
    booking.dropoff_at = now
    booking.completed_at = now
    booking.updated_at = now
    booking.save(update_fields=['booking_status', 'ride_status', 'dropoff_at', 'completed_at', 'updated_at'])

    try:
        RideAuditEvent.objects.create(
            trip=booking.trip,
            booking=booking,
            actor=booking.passenger,
            event_type='PASSENGER_DROPPED_OFF',
            payload={
                'booking_id': booking.id,
                'passenger_id': passenger_id,
                'timestamp': now.isoformat(),
            },
        )
    except Exception:
        pass

    try:
        trip = booking.trip
        payload = {
            'user_id': str(trip.driver_id),
            'driver_id': str(trip.driver_id),
            'title': 'Passenger dropped off',
            'body': f'Passenger {booking.passenger_id} reached destination.',
            'data': {
                'type': 'passenger_dropped_off',
                'trip_id': str(trip.trip_id),
                'booking_id': str(booking.id),
            },
        }
        send_ride_notification_async(payload)
    except Exception:
        pass

    return JsonResponse({'success': True, 'booking_id': booking.id, 'trip_id': booking.trip.trip_id, 'ride_status': booking.ride_status, 'booking_status': booking.booking_status})


@csrf_exempt
@require_http_methods(["POST"])
def driver_mark_reached_pickup(request, booking_id):
    try:
        booking = Booking.objects.select_related('trip', 'trip__driver', 'passenger', 'from_stop').get(id=booking_id)
    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    driver_id = _coerce_int(data.get('driver_id'))
    if driver_id is None or booking.trip.driver_id != driver_id:
        return JsonResponse({'success': False, 'error': 'Not authorized as driver'}, status=403)

    now = timezone.now()
    _set_trip_booking_flag(booking.trip, booking.id, 'driver_reached_pickup_at', now.isoformat())

    try:
        RideAuditEvent.objects.create(
            trip=booking.trip,
            booking=booking,
            actor=booking.trip.driver,
            event_type='DRIVER_REACHED_PICKUP',
            payload={'booking_id': booking.id, 'timestamp': now.isoformat()},
        )
    except Exception:
        pass

    try:
        pickup_name = getattr(getattr(booking, 'from_stop', None), 'stop_name', None) or 'pickup point'
        payload = {
            'user_id': str(booking.passenger_id),
            'driver_id': str(booking.trip.driver_id),
            'title': 'Driver arrived',
            'body': f'Driver reached {pickup_name}.',
            'data': {
                'type': 'driver_reached_pickup',
                'trip_id': str(booking.trip.trip_id),
                'booking_id': str(booking.id),
            },
        }
        send_ride_notification_async(payload)
    except Exception:
        pass

    return JsonResponse({'success': True, 'booking_id': booking.id, 'trip_id': booking.trip.trip_id})


@csrf_exempt
@require_http_methods(["POST"])
def driver_mark_reached_dropoff(request, booking_id):
    try:
        booking = Booking.objects.select_related('trip', 'trip__driver', 'passenger', 'to_stop').get(id=booking_id)
    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    driver_id = _coerce_int(data.get('driver_id'))
    if driver_id is None or booking.trip.driver_id != driver_id:
        return JsonResponse({'success': False, 'error': 'Not authorized as driver'}, status=403)

    now = timezone.now()

    # Mark booking reached/dropped-off (driver side)
    try:
        booking.booking_status = 'COMPLETED'
        booking.ride_status = 'DROPPED_OFF'
        booking.dropoff_at = now
        booking.completed_at = now
        booking.updated_at = now
        booking.save(update_fields=['booking_status', 'ride_status', 'dropoff_at', 'completed_at', 'updated_at'])
    except Exception:
        pass

    _set_trip_booking_flag(booking.trip, booking.id, 'driver_reached_dropoff_at', now.isoformat())

    try:
        RideAuditEvent.objects.create(
            trip=booking.trip,
            booking=booking,
            actor=booking.trip.driver,
            event_type='DRIVER_REACHED_DROPOFF',
            payload={'booking_id': booking.id, 'timestamp': now.isoformat()},
        )
    except Exception:
        pass

    try:
        drop_name = getattr(getattr(booking, 'to_stop', None), 'stop_name', None) or 'destination'
        payload_passenger = {
            'user_id': str(booking.passenger_id),
            'driver_id': str(booking.trip.driver_id),
            'title': 'Reached destination',
            'body': f'You reached {drop_name}. Please proceed to payment.',
            'data': {
                'type': 'driver_reached_dropoff',
                'trip_id': str(booking.trip.trip_id),
                'booking_id': str(booking.id),
            },
        }
        send_ride_notification_async(payload_passenger)
    except Exception:
        pass

    try:
        payload_driver = {
            'user_id': str(booking.trip.driver_id),
            'driver_id': str(booking.trip.driver_id),
            'title': 'Drop-off reached',
            'body': f'Drop-off completed for booking {booking.id}.',
            'data': {
                'type': 'driver_dropoff_completed',
                'trip_id': str(booking.trip.trip_id),
                'booking_id': str(booking.id),
            },
        }
        send_ride_notification_async(payload_driver)
    except Exception:
        pass

    return JsonResponse({'success': True, 'booking_id': booking.id, 'trip_id': booking.trip.trip_id})


@csrf_exempt
@require_http_methods(["GET"])
def get_booking_payment_details(request, booking_id):
    role = (request.GET.get('role') or '').upper().strip()
    user_id = _coerce_int(request.GET.get('user_id'))

    if role not in ['DRIVER', 'PASSENGER'] or user_id is None:
        return JsonResponse({'success': False, 'error': 'role and user_id are required'}, status=400)

    try:
        booking = (
            Booking.objects
            .select_related('trip', 'trip__driver', 'passenger')
            .only(
                'id', 'payment_status', 'booking_status',
                'driver_rating', 'driver_feedback',
                'passenger_rating', 'passenger_feedback',
                'trip__trip_id', 'trip__driver__id', 'trip__driver__bankname', 'trip__driver__accountno', 'trip__driver__accountqr_url',
                'passenger__id', 'passenger__name',
            )
            .get(id=booking_id)
        )
    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)

    if role == 'DRIVER':
        if booking.trip.driver_id != user_id:
            return JsonResponse({'success': False, 'error': 'Not authorized as driver'}, status=403)
    else:
        if booking.passenger_id != user_id:
            return JsonResponse({'success': False, 'error': 'Not authorized as passenger'}, status=403)

    payment = None
    try:
        p = (
            TripPayment.objects
            .filter(booking=booking)
            .only('id', 'payment_method', 'amount', 'currency', 'payment_status', 'receipt_url', 'created_at', 'completed_at')
            .order_by('-created_at')
            .first()
        )
        if p is not None:
            payment = p.get_payment_summary()
    except Exception:
        payment = None

    driver = booking.trip.driver
    return JsonResponse({
        'success': True,
        'booking': {
            'id': booking.id,
            'trip_id': booking.trip.trip_id,
            'payment_status': booking.payment_status,
            'booking_status': booking.booking_status,
            'driver_rating': float(booking.driver_rating) if booking.driver_rating else None,
            'driver_feedback': booking.driver_feedback,
            'passenger_rating': float(booking.passenger_rating) if booking.passenger_rating else None,
            'passenger_feedback': booking.passenger_feedback,
        },
        'driver_bank': {
            'bankname': getattr(driver, 'bankname', None),
            'accountno': getattr(driver, 'accountno', None),
            'iban': getattr(driver, 'iban', None),
            'accountqr_url': getattr(driver, 'accountqr_url', None),
        },
        'passenger': {
            'id': booking.passenger.id,
            'name': booking.passenger.name,
        },
        'payment': payment,
    })

@csrf_exempt
@require_http_methods(["POST"])
def submit_booking_payment(request, booking_id):
    try:
        booking = Booking.objects.select_related('trip', 'trip__driver', 'passenger').get(id=booking_id)
    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)

    passenger_id = _coerce_int(request.POST.get('passenger_id'))
    if passenger_id is None or booking.passenger_id != passenger_id:
        return JsonResponse({'success': False, 'error': 'Not authorized as passenger'}, status=403)

    rating_raw = request.POST.get('driver_rating')
    feedback = (request.POST.get('driver_feedback') or '').strip()

    try:
        rating = float(rating_raw) if rating_raw is not None and str(rating_raw).strip() != '' else None
    except Exception:
        rating = None

    if rating is None or rating < 1.0 or rating > 5.0:
        return JsonResponse({'success': False, 'error': 'driver_rating must be between 1 and 5'}, status=400)

    payment_method = (request.POST.get('payment_method') or 'BANK_TRANSFER').strip().upper()
    if payment_method not in ['BANK_TRANSFER', 'CASH']:
        return JsonResponse({'success': False, 'error': 'payment_method must be BANK_TRANSFER or CASH'}, status=400)

    receipt_url = None
    receipt_file = request.FILES.get('receipt')
    if payment_method != 'CASH':
        if receipt_file is None:
            return JsonResponse({'success': False, 'error': 'receipt file is required'}, status=400)

        bucket = getattr(settings, 'SUPABASE_PAYMENT_BUCKET', None) or getattr(settings, 'SUPABASE_USER_BUCKET', 'user-images')
        safe_name = (getattr(receipt_file, 'name', '') or 'receipt').replace('..', '.').replace('\\', '_').replace('/', '_')
        ts = timezone.now().strftime('%Y%m%d_%H%M%S')
        dest_path = f"payment_receipts/booking_{booking.id}/{ts}_{safe_name}"

        try:
            receipt_url = upload_to_supabase(bucket, receipt_file, dest_path)
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)

    now = timezone.now()
    try:
        payment, _ = TripPayment.objects.get_or_create(
            booking=booking,
            defaults={
                'payment_method': payment_method,
                'amount': booking.total_fare,
                'currency': 'PKR',
                'payment_status': 'PENDING',
            },
        )
        payment.receipt_url = receipt_url
        payment.payment_method = payment_method
        payment.payment_status = 'PENDING'
        payment.updated_at = now
        payment.save(update_fields=['receipt_url', 'payment_method', 'payment_status', 'updated_at'])
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

    booking.driver_rating = rating
    booking.driver_feedback = feedback
    booking.updated_at = now
    booking.save(update_fields=['driver_rating', 'driver_feedback', 'updated_at'])

    try:
        method_label = 'Cash' if payment_method == 'CASH' else 'Receipt uploaded'
        payload = {
            'user_id': str(booking.trip.driver_id),
            'driver_id': str(booking.trip.driver_id),
            'title': 'Payment submitted',
            'body': f'Passenger submitted payment ({method_label}).',
            'data': {
                'type': 'payment_submitted',
                'trip_id': str(booking.trip.trip_id),
                'booking_id': str(booking.id),
                'payment_method': payment_method,
            },
        }
        send_ride_notification_async(payload)
    except Exception:
        pass

    return JsonResponse({
        'success': True,
        'booking_id': booking.id,
        'trip_id': booking.trip.trip_id,
        'receipt_url': receipt_url,
        'payment_method': payment_method,
    })


@csrf_exempt
@require_http_methods(["POST"])
def confirm_booking_payment(request, booking_id):
    try:
        booking = Booking.objects.select_related('trip', 'trip__driver', 'passenger').get(id=booking_id)
    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    driver_id = _coerce_int(data.get('driver_id'))
    if driver_id is None or booking.trip.driver_id != driver_id:
        return JsonResponse({'success': False, 'error': 'Not authorized as driver'}, status=403)

    rating_raw = data.get('passenger_rating')
    feedback = (data.get('passenger_feedback') or '').strip()
    received = data.get('received')
    if received is not True:
        return JsonResponse({'success': False, 'error': 'received must be true'}, status=400)

    try:
        rating = float(rating_raw) if rating_raw is not None and str(rating_raw).strip() != '' else None
    except Exception:
        rating = None

    if rating is None or rating < 1.0 or rating > 5.0:
        return JsonResponse({'success': False, 'error': 'passenger_rating must be between 1 and 5'}, status=400)

    now = timezone.now()

    booking.passenger_rating = rating
    booking.passenger_feedback = feedback
    booking.updated_at = now
    booking.save(update_fields=['passenger_rating', 'passenger_feedback', 'updated_at'])

    try:
        payment = TripPayment.objects.filter(booking=booking).order_by('-created_at').first()
        if payment is None:
            payment = TripPayment.objects.create(
                booking=booking,
                payment_method='BANK_TRANSFER',
                amount=booking.total_fare,
                currency='PKR',
                payment_status='PENDING',
            )
        payment.payment_status = 'COMPLETED'
        payment.completed_at = now
        payment.updated_at = now
        payment.save(update_fields=['payment_status', 'completed_at', 'updated_at'])
        booking.update_payment_status('COMPLETED')
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

    return JsonResponse({'success': True, 'booking_id': booking.id, 'payment_status': booking.payment_status})


@csrf_exempt
@require_http_methods(["GET"])
def get_trip_payments(request, trip_id):
    trip, error = _get_trip_or_404(trip_id)
    if error is not None:
        return error

    driver_id = _coerce_int(request.GET.get('driver_id'))
    if driver_id is None or trip.driver_id != driver_id:
        return JsonResponse({'success': False, 'error': 'Not authorized as driver'}, status=403)

    bookings = (
        Booking.objects
        .filter(trip=trip, booking_status__in=['CONFIRMED', 'COMPLETED'])
        .select_related('passenger')
        .only(
            'id', 'payment_status', 'booking_status',
            'driver_rating', 'driver_feedback',
            'passenger_rating', 'passenger_feedback',
            'passenger__id', 'passenger__name',
        )
        .order_by('id')
    )

    booking_ids = [b.id for b in bookings]
    payment_map = {}
    try:
        payments = (
            TripPayment.objects
            .filter(booking_id__in=booking_ids)
            .only('booking_id', 'receipt_url', 'payment_method', 'payment_status', 'created_at', 'completed_at')
            .order_by('-created_at')
        )
        for p in payments:
            if p.booking_id not in payment_map:
                payment_map[p.booking_id] = p
    except Exception:
        payment_map = {}

    result = []
    for b in bookings:
        p = payment_map.get(b.id)
        result.append({
            'booking_id': b.id,
            'booking_status': b.booking_status,
            'payment_status': b.payment_status,
            'receipt_url': getattr(p, 'receipt_url', None) if p is not None else None,
            'payment_method': getattr(p, 'payment_method', None) if p is not None else None,
            'passenger': {
                'id': b.passenger.id,
                'name': b.passenger.name,
            },
            'passenger_rating': float(b.passenger_rating) if b.passenger_rating else None,
            'passenger_feedback': b.passenger_feedback,
            'driver_rating': float(b.driver_rating) if b.driver_rating else None,
            'driver_feedback': b.driver_feedback,
        })

    return JsonResponse({'success': True, 'trip_id': trip.trip_id, 'payments': result})


@csrf_exempt
@require_http_methods(["POST"])
def update_live_location(request, trip_id):
    """Update live location for driver or passenger; persists for admin monitoring."""
    trip, error = _get_trip_or_404(trip_id)
    if error is not None:
        return error

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    user_id = _coerce_int(data.get('user_id'))
    role = data.get('role')
    lat = data.get('lat')
    lng = data.get('lng')
    speed = data.get('speed')

    if user_id is None or role not in ['DRIVER', 'PASSENGER'] or lat is None or lng is None:
        return JsonResponse({'success': False, 'error': 'Missing or invalid fields'}, status=400)

    if role == 'DRIVER' and trip.trip_status != 'IN_PROGRESS':
        # This can happen briefly after ending/cancelling a trip while background tracking is still flushing.
        return JsonResponse({'success': True, 'ignored': True, 'reason': 'Trip not in progress'})

    state = trip.live_tracking_state or {}
    passengers = state.get('passengers', [])
    now_iso = timezone.now().isoformat()
    now_dt = timezone.now()

    if role == 'DRIVER':
        if user_id is None or trip.driver_id != user_id:
            return JsonResponse({'success': False, 'error': 'Not authorized as driver'}, status=403)

        # Store the actual traveled path for map display (thinned to avoid unbounded growth).
        try:
            driver_path = state.get('driver_path')
            if not isinstance(driver_path, list):
                driver_path = []

            last = driver_path[-1] if driver_path else None
            last_lat = last.get('lat') if isinstance(last, dict) else None
            last_lng = last.get('lng') if isinstance(last, dict) else None
            last_ts = last.get('timestamp') if isinstance(last, dict) else None

            append = False
            if last_lat is None or last_lng is None:
                append = True
            else:
                dist_m = _haversine_meters(last_lat, last_lng, lat, lng)
                # Append if moved >= 12m, or if timestamp changed and we have no distance.
                if dist_m is None:
                    append = (last_ts != now_iso)
                else:
                    append = dist_m >= 12.0

            if append:
                driver_path.append({
                    'lat': lat,
                    'lng': lng,
                    'speed': speed,
                    'timestamp': now_iso,
                })

                # Keep only the latest N points to avoid huge JSON payloads.
                max_points = 2000
                if len(driver_path) > max_points:
                    driver_path = driver_path[-max_points:]

            state['driver_path'] = driver_path
        except Exception:
            pass

        state['driver'] = {
            'user_id': user_id,
            'lat': lat,
            'lng': lng,
            'speed': speed,
            'timestamp': now_iso,
        }

        try:
            TripLiveLocationUpdate.objects.create(
                trip=trip,
                user=trip.driver,
                booking=None,
                role='DRIVER',
                latitude=lat,
                longitude=lng,
                speed_mps=speed,
                recorded_at=now_dt,
            )
        except Exception:
            pass
    else:
        booking_id = _coerce_int(data.get('booking_id'))
        if booking_id is None:
            return JsonResponse({'success': False, 'error': 'booking_id required for passenger'}, status=400)
        try:
            booking = Booking.objects.select_related('passenger').get(id=booking_id, trip=trip, passenger_id=user_id)
        except Booking.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Invalid booking for passenger'}, status=404)

        if getattr(booking, 'ride_status', None) != 'RIDE_STARTED':
            # Passenger can stop sending once dropped off; ignore any late flushes.
            return JsonResponse({'success': True, 'ignored': True, 'reason': 'Passenger not on board'})

        updated = False
        for p in passengers:
            try:
                existing_bid = _coerce_int(p.get('booking_id')) if isinstance(p, dict) else None
            except Exception:
                existing_bid = None
            if existing_bid is not None and existing_bid == booking_id:
                p.update({
                    'booking_id': booking_id,
                    'user_id': user_id,
                    'lat': lat,
                    'lng': lng,
                    'speed': speed,
                    'timestamp': now_iso,
                })
                updated = True
                break
        if not updated:
            passengers.append({
                'booking_id': booking_id,
                'user_id': user_id,
                'lat': lat,
                'lng': lng,
                'speed': speed,
                'timestamp': now_iso,
            })
        state['passengers'] = passengers

        try:
            TripLiveLocationUpdate.objects.create(
                trip=trip,
                user=booking.passenger,
                booking=booking,
                role='PASSENGER',
                latitude=lat,
                longitude=lng,
                speed_mps=speed,
                recorded_at=now_dt,
            )
        except Exception:
            pass

    state['last_update'] = now_iso
    trip.live_tracking_state = state
    trip.save(update_fields=['live_tracking_state'])

    return JsonResponse({'success': True})


@csrf_exempt
@require_http_methods(["GET"])
def get_live_location(request, trip_id):
    """Return latest live tracking snapshot for the trip for passenger/driver/admin."""
    trip, error = _get_trip_or_404(trip_id)
    if error is not None:
        return error

    requester_role = (request.GET.get('role') or '').upper().strip()
    requester_user_id = request.GET.get('user_id')
    requester_booking_id = request.GET.get('booking_id')

    requester_booking = None

    if requester_role in ['DRIVER', 'PASSENGER'] and requester_user_id is not None:
        try:
            requester_user_id_int = int(requester_user_id)
        except Exception:
            return JsonResponse({'success': False, 'error': 'Invalid user_id'}, status=400)

        if requester_role == 'DRIVER':
            if trip.driver_id != requester_user_id_int:
                return JsonResponse({'success': False, 'error': 'Not authorized as driver'}, status=403)
        else:
            if requester_booking_id is None:
                return JsonResponse({'success': False, 'error': 'booking_id required for passenger'}, status=400)
            try:
                requester_booking_id_int = int(requester_booking_id)
            except Exception:
                return JsonResponse({'success': False, 'error': 'Invalid booking_id'}, status=400)
            try:
                requester_booking = Booking.objects.select_related('from_stop', 'to_stop', 'passenger').get(
                    id=requester_booking_id_int,
                    trip=trip,
                    passenger_id=requester_user_id_int,
                    booking_status__in=['CONFIRMED', 'COMPLETED'],
                )
            except Booking.DoesNotExist:
                return JsonResponse({'success': False, 'error': 'Not authorized for this trip'}, status=403)

    live_state = trip.live_tracking_state or {}
    if requester_role == 'PASSENGER' and isinstance(live_state, dict):
        try:
            booking_id_int = int(requester_booking_id) if requester_booking_id is not None else None
        except Exception:
            booking_id_int = None
        if booking_id_int is not None:
            passengers = live_state.get('passengers')
            if isinstance(passengers, list):
                live_state = dict(live_state)
                live_state['passengers'] = [
                    p for p in passengers
                    if isinstance(p, dict) and p.get('booking_id') == booking_id_int
                ]

    if isinstance(live_state, dict):
        try:
            passengers = live_state.get('passengers')
            if isinstance(passengers, list) and passengers:
                booking_ids = []
                for p in passengers:
                    if isinstance(p, dict):
                        bid = p.get('booking_id')
                        try:
                            bid_int = int(bid) if bid is not None else None
                        except Exception:
                            bid_int = None
                        if bid_int is not None:
                            booking_ids.append(bid_int)

                if booking_ids:
                    bookings = (
                        Booking.objects
                        .filter(trip=trip, id__in=booking_ids)
                        .select_related('passenger', 'to_stop')
                        .only(
                            'id',
                            'passenger__id', 'passenger__name', 'passenger__profile_photo_url',
                            'to_stop__stop_name', 'to_stop__stop_order', 'to_stop__latitude', 'to_stop__longitude',
                        )
                    )
                    booking_map = {b.id: b for b in bookings}

                    updated = []
                    for p in passengers:
                        if not isinstance(p, dict):
                            continue
                        p2 = dict(p)
                        bid = p2.get('booking_id')
                        try:
                            bid_int = int(bid) if bid is not None else None
                        except Exception:
                            bid_int = None
                        if bid_int is not None and bid_int in booking_map:
                            b = booking_map[bid_int]
                            p2['name'] = getattr(getattr(b, 'passenger', None), 'name', None)
                            p2['profile_photo'] = getattr(getattr(b, 'passenger', None), 'profile_photo_url', None)
                            p2['passenger_id'] = getattr(getattr(b, 'passenger', None), 'id', None)
                            try:
                                p2['dropoff_stop_name'] = getattr(getattr(b, 'to_stop', None), 'stop_name', None)
                                p2['dropoff_stop_order'] = getattr(getattr(b, 'to_stop', None), 'stop_order', None)
                                p2['dropoff_lat'] = float(getattr(getattr(b, 'to_stop', None), 'latitude', None)) if getattr(getattr(b, 'to_stop', None), 'latitude', None) is not None else None
                                p2['dropoff_lng'] = float(getattr(getattr(b, 'to_stop', None), 'longitude', None)) if getattr(getattr(b, 'to_stop', None), 'longitude', None) is not None else None
                            except Exception:
                                pass
                        updated.append(p2)
                    live_state = dict(live_state)
                    live_state['passengers'] = updated
        except Exception:
            pass
    now = timezone.now()

    driver_speed_mps = None
    driver_speed_kph = None
    driver_distance_to_final_m = None
    driver_eta_seconds_to_final = None
    driver_eta_at = None

    passenger_distance_to_dropoff_m = None
    passenger_eta_seconds_to_dropoff = None
    passenger_eta_at = None

    driver_meta = {
        'signal_lost': True,
        'last_seen_seconds': None,
        'is_deviating': None,
        'deviation_meters': None,
    }

    driver_obj = live_state.get('driver') if isinstance(live_state, dict) else None
    driver_ts = None
    if isinstance(driver_obj, dict):
        ts_raw = driver_obj.get('timestamp')
        driver_ts = _parse_iso_dt(ts_raw)

    if driver_ts is None:
        try:
            latest = (
                TripLiveLocationUpdate.objects
                .filter(trip=trip, role='DRIVER')
                .only('recorded_at')
                .first()
            )
            if latest is not None:
                driver_ts = latest.recorded_at
        except Exception:
            driver_ts = None

    if driver_ts is not None:
        seconds = max(int((now - driver_ts).total_seconds()), 0)
        driver_meta['last_seen_seconds'] = seconds
        driver_meta['signal_lost'] = seconds > 30

    if isinstance(driver_obj, dict):
        driver_speed_mps = _coerce_float(driver_obj.get('speed'))

    if driver_speed_mps is None and isinstance(live_state, dict):
        try:
            path = live_state.get('driver_path')
            if isinstance(path, list) and len(path) >= 2:
                a = path[-2] if isinstance(path[-2], dict) else None
                b = path[-1] if isinstance(path[-1], dict) else None
                if a and b:
                    a_ts = _parse_iso_dt(a.get('timestamp'))
                    b_ts = _parse_iso_dt(b.get('timestamp'))
                    if a_ts and b_ts:
                        dt_s = (b_ts - a_ts).total_seconds()
                        if dt_s > 0.1:
                            d_m = _haversine_meters(a.get('lat'), a.get('lng'), b.get('lat'), b.get('lng'))
                            if d_m is not None:
                                driver_speed_mps = float(d_m) / float(dt_s)
        except Exception:
            pass

    if driver_speed_mps is not None and driver_speed_mps > 0:
        driver_speed_kph = float(driver_speed_mps) * 3.6

    try:
        if isinstance(driver_obj, dict) and driver_obj.get('lat') is not None and driver_obj.get('lng') is not None:
            min_d = None

            try:
                geom = getattr(getattr(trip, 'route', None), 'route_geometry', None)
            except Exception:
                geom = None
            if isinstance(geom, list) and geom:
                for p in geom:
                    if not isinstance(p, dict):
                        continue
                    d = _haversine_meters(driver_obj.get('lat'), driver_obj.get('lng'), p.get('lat'), p.get('lng'))
                    if d is None:
                        continue
                    if min_d is None or d < min_d:
                        min_d = d
            else:
                stops = list(
                    RouteStop.objects
                    .filter(route=trip.route)
                    .exclude(latitude__isnull=True)
                    .exclude(longitude__isnull=True)
                    .only('latitude', 'longitude')
                )
                for s in stops:
                    d = _haversine_meters(driver_obj.get('lat'), driver_obj.get('lng'), s.latitude, s.longitude)
                    if d is None:
                        continue
                    if min_d is None or d < min_d:
                        min_d = d

            if min_d is not None:
                driver_meta['deviation_meters'] = float(min_d)
                driver_meta['is_deviating'] = min_d > 300
    except Exception:
        pass

    try:
        if isinstance(driver_obj, dict) and driver_obj.get('lat') is not None and driver_obj.get('lng') is not None:
            last_stop = (
                RouteStop.objects
                .filter(route=trip.route)
                .exclude(latitude__isnull=True)
                .exclude(longitude__isnull=True)
                .only('latitude', 'longitude')
                .order_by('-stop_order')
                .first()
            )
            if last_stop is not None:
                d_m = _haversine_meters(driver_obj.get('lat'), driver_obj.get('lng'), last_stop.latitude, last_stop.longitude)
                if d_m is not None:
                    driver_distance_to_final_m = float(d_m)
                    if driver_speed_mps is not None and driver_speed_mps >= 0.5:
                        driver_eta_seconds_to_final = int(driver_distance_to_final_m / float(driver_speed_mps))
                        driver_eta_at = (now + timedelta(seconds=driver_eta_seconds_to_final)).isoformat()
    except Exception:
        pass

    try:
        if requester_role == 'PASSENGER' and requester_booking is not None and isinstance(driver_obj, dict):
            to_stop = getattr(requester_booking, 'to_stop', None)
            if to_stop is not None and driver_obj.get('lat') is not None and driver_obj.get('lng') is not None:
                d_m = _haversine_meters(driver_obj.get('lat'), driver_obj.get('lng'), getattr(to_stop, 'latitude', None), getattr(to_stop, 'longitude', None))
                if d_m is not None:
                    passenger_distance_to_dropoff_m = float(d_m)
                    if driver_speed_mps is not None and driver_speed_mps >= 0.5:
                        passenger_eta_seconds_to_dropoff = int(passenger_distance_to_dropoff_m / float(driver_speed_mps))
                        passenger_eta_at = (now + timedelta(seconds=passenger_eta_seconds_to_dropoff)).isoformat()
    except Exception:
        pass

    try:
        if requester_role == 'PASSENGER' and requester_booking is not None and isinstance(driver_obj, dict):
            if driver_obj.get('lat') is None or driver_obj.get('lng') is None:
                raise Exception('missing driver coordinates')

            pickup_verified = getattr(requester_booking, 'pickup_verified_at', None) is not None
            ride_status = getattr(requester_booking, 'ride_status', None)

            if not pickup_verified:
                from_stop = getattr(requester_booking, 'from_stop', None)
                if from_stop is not None and getattr(from_stop, 'latitude', None) is not None and getattr(from_stop, 'longitude', None) is not None:
                    d_pick = _haversine_meters(driver_obj.get('lat'), driver_obj.get('lng'), getattr(from_stop, 'latitude', None), getattr(from_stop, 'longitude', None))
                    if d_pick is not None and float(d_pick) <= 600.0:
                        key = f"passenger_near_pickup_{requester_booking.id}"
                        if _record_system_notification_if_due(trip, key, cooldown_seconds=300):
                            payload = {
                                'user_id': str(requester_booking.passenger_id),
                                'driver_id': str(trip.driver_id),
                                'title': 'Driver almost reached',
                                'body': 'Your driver almost reached to pick you.',
                                'data': {
                                    'type': 'driver_near_pickup',
                                    'trip_id': str(trip.trip_id),
                                    'booking_id': str(requester_booking.id),
                                },
                            }
                            try:
                                send_ride_notification_async(payload)
                            except Exception:
                                pass

            if ride_status == 'RIDE_STARTED' and passenger_distance_to_dropoff_m is not None:
                if float(passenger_distance_to_dropoff_m) <= 800.0:
                    key = f"passenger_near_destination_{requester_booking.id}"
                    if _record_system_notification_if_due(trip, key, cooldown_seconds=600):
                        payload = {
                            'user_id': str(requester_booking.passenger_id),
                            'driver_id': str(trip.driver_id),
                            'title': 'Near destination',
                            'body': 'You are near your destination.',
                            'data': {
                                'type': 'near_destination',
                                'trip_id': str(trip.trip_id),
                                'booking_id': str(requester_booking.id),
                            },
                        }
                        try:
                            send_ride_notification_async(payload)
                        except Exception:
                            pass
    except Exception:
        pass

    try:
        if requester_role == 'DRIVER' and isinstance(live_state, dict) and isinstance(driver_obj, dict):
            driver_lat = driver_obj.get('lat')
            driver_lng = driver_obj.get('lng')
            if driver_lat is not None and driver_lng is not None:
                ps = live_state.get('passengers')
                if isinstance(ps, list) and ps:
                    enriched = []
                    for p in ps:
                        if not isinstance(p, dict):
                            continue
                        p2 = dict(p)
                        d_lat = p2.get('dropoff_lat')
                        d_lng = p2.get('dropoff_lng')
                        if d_lat is not None and d_lng is not None:
                            d_m = _haversine_meters(driver_lat, driver_lng, d_lat, d_lng)
                            if d_m is not None:
                                p2['distance_to_dropoff_m'] = float(d_m)
                                if driver_speed_mps is not None and driver_speed_mps >= 0.5:
                                    p2['eta_seconds_to_dropoff'] = int(float(d_m) / float(driver_speed_mps))
                        enriched.append(p2)
                    live_state = dict(live_state)
                    live_state['passengers'] = enriched
    except Exception:
        pass

    return JsonResponse({
        'success': True,
        'trip_id': trip.trip_id,
        'trip_status': trip.trip_status,
        'booking_ride_status': requester_booking.ride_status if requester_booking is not None else None,
        'booking_status': requester_booking.booking_status if requester_booking is not None else None,
        'pickup_verified': True if (requester_booking is not None and getattr(requester_booking, 'pickup_verified_at', None) is not None) else False,
        'live_state': live_state,
        'driver_meta': driver_meta,
        'runtime': {
            'driver_speed_mps': driver_speed_mps,
            'driver_speed_kph': driver_speed_kph,
            'driver_distance_to_final_m': driver_distance_to_final_m,
            'driver_eta_seconds_to_final': driver_eta_seconds_to_final,
            'driver_eta_at': driver_eta_at,
            'passenger_distance_to_dropoff_m': passenger_distance_to_dropoff_m,
            'passenger_eta_seconds_to_dropoff': passenger_eta_seconds_to_dropoff,
            'passenger_eta_at': passenger_eta_at,
        },
    })


@csrf_exempt
@require_http_methods(["POST"])
def generate_pickup_code(request, trip_id, booking_id):
    trip, error = _get_trip_or_404(trip_id)
    if error is not None:
        return error

    try:
        booking = Booking.objects.select_related('passenger', 'from_stop').get(id=booking_id, trip=trip)
    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found for this trip'}, status=404)

    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    driver_id = _coerce_int(data.get('driver_id'))
    driver_lat = data.get('driver_lat')
    driver_lng = data.get('driver_lng')
    if driver_id is None or trip.driver_id != driver_id:
        return JsonResponse({'success': False, 'error': 'Not authorized as driver for this trip'}, status=403)

    try:
        generations_count = PickupCodeVerification.objects.filter(
            trip=trip,
            booking=booking,
            driver_id=driver_id,
        ).count()
        if generations_count >= 5:
            return JsonResponse({'success': False, 'error': 'Pickup code generation limit reached. Please contact support if needed.'}, status=429)
    except Exception:
        pass

    pickup_lat = getattr(booking.from_stop, 'latitude', None)
    pickup_lng = getattr(booking.from_stop, 'longitude', None)
    if pickup_lat is None or pickup_lng is None:
        return JsonResponse({'success': False, 'error': 'Pickup location is missing coordinates'}, status=400)

    if driver_lat is None or driver_lng is None:
        try:
            latest_driver = (
                TripLiveLocationUpdate.objects
                .filter(trip=trip, role='DRIVER')
                .only('latitude', 'longitude')
                .order_by('-recorded_at')
                .first()
            )
            if latest_driver is not None:
                driver_lat = latest_driver.latitude
                driver_lng = latest_driver.longitude
        except Exception:
            pass

    if driver_lat is None or driver_lng is None:
        return JsonResponse({'success': False, 'error': 'Driver location is required to generate pickup code'}, status=400)

    dist = _haversine_meters(driver_lat, driver_lng, pickup_lat, pickup_lng)
    if dist is None:
        return JsonResponse({'success': False, 'error': 'Invalid location data'}, status=400)

    # Invalidate previous active codes for this booking
    PickupCodeVerification.objects.filter(booking=booking, status='ACTIVE').update(status='FAILED')

    # Generate a 6-digit numeric code
    import random
    code = f"{random.randint(0, 999999):06d}"

    now = timezone.now()
    expires_at = now + timedelta(minutes=5)

    pickup_code = PickupCodeVerification(
        booking=booking,
        trip=trip,
        driver_id=driver_id,
        passenger_id=booking.passenger_id,
        expires_at=expires_at,
        max_attempts=3,
        driver_latitude=driver_lat,
        driver_longitude=driver_lng,
    )
    pickup_code.set_code(code)
    pickup_code.save()

    try:
        RideAuditEvent.objects.create(
            trip=trip,
            booking=booking,
            actor=trip.driver,
            event_type='PICKUP_CODE_GENERATED',
            payload={
                'booking_id': booking.id,
                'passenger_id': booking.passenger_id,
                'expires_at': expires_at.isoformat(),
                'driver_lat': driver_lat,
                'driver_lng': driver_lng,
                'distance_to_pickup_m': float(dist),
            },
        )
    except Exception:
        pass

    return JsonResponse({
        'success': True,
        'booking_id': booking.id,
        'trip_id': trip.trip_id,
        'code': code,
        'expires_at': expires_at.isoformat(),
        'max_attempts': pickup_code.max_attempts,
    })


@csrf_exempt
@require_http_methods(["POST"])
def verify_pickup_code(request):
    try:
        data = json.loads(request.body.decode('utf-8'))
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    booking_id = _coerce_int(data.get('booking_id'))
    passenger_id = _coerce_int(data.get('passenger_id'))
    code = data.get('code')
    passenger_lat = data.get('passenger_lat')
    passenger_lng = data.get('passenger_lng')

    if booking_id is None or passenger_id is None or not code:
        return JsonResponse({'success': False, 'error': 'Missing required fields'}, status=400)

    try:
        booking = Booking.objects.select_related('trip', 'passenger').get(id=booking_id, passenger_id=passenger_id)
    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)

    if booking.booking_status != 'CONFIRMED':
        return JsonResponse({'success': False, 'error': f'Booking is not confirmed (status={booking.booking_status})'}, status=400)

    try:
        pickup_code = (
            PickupCodeVerification.objects
            .filter(booking=booking, status='ACTIVE')
            .latest('generated_at')
        )
    except PickupCodeVerification.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'No active pickup code. Please ask driver to generate a new code.'}, status=400)

    now = timezone.now()
    if now > pickup_code.expires_at:
        pickup_code.status = 'EXPIRED'
        pickup_code.last_attempt_at = now
        pickup_code.verification_result = 'expired'
        pickup_code.save(update_fields=['status', 'last_attempt_at', 'verification_result'])

        try:
            RideAuditEvent.objects.create(
                trip=booking.trip,
                booking=booking,
                actor=booking.passenger,
                event_type='PICKUP_CODE_VERIFY_EXPIRED',
                payload={'booking_id': booking.id, 'passenger_id': passenger_id, 'timestamp': now.isoformat()},
            )
        except Exception:
            pass

        return JsonResponse({'success': False, 'error': 'Pickup code expired. Please ask driver to generate a new code.'}, status=400)

    if pickup_code.attempts >= pickup_code.max_attempts:
        pickup_code.status = 'FAILED'
        pickup_code.last_attempt_at = now
        pickup_code.verification_result = 'max_attempts'
        pickup_code.save(update_fields=['status', 'last_attempt_at', 'verification_result'])

        try:
            RideAuditEvent.objects.create(
                trip=booking.trip,
                booking=booking,
                actor=booking.passenger,
                event_type='PICKUP_CODE_VERIFY_MAX_ATTEMPTS',
                payload={'booking_id': booking.id, 'passenger_id': passenger_id, 'timestamp': now.isoformat()},
            )
        except Exception:
            pass

        return JsonResponse({'success': False, 'error': 'Maximum attempts reached. Please ask driver to generate a new code.'}, status=400)

    try:
        pickup_stop = booking.from_stop
        pickup_lat = getattr(pickup_stop, 'latitude', None)
        pickup_lng = getattr(pickup_stop, 'longitude', None)
        if pickup_lat is not None and pickup_lng is not None:
            latest_driver = (
                TripLiveLocationUpdate.objects
                .filter(trip=booking.trip, role='DRIVER')
                .only('latitude', 'longitude', 'recorded_at')
                .order_by('-recorded_at')
                .first()
            )
            driver_lat = latest_driver.latitude if latest_driver is not None else pickup_code.driver_latitude
            driver_lng = latest_driver.longitude if latest_driver is not None else pickup_code.driver_longitude
            if driver_lat is not None and driver_lng is not None:
                dist = _haversine_meters(driver_lat, driver_lng, pickup_lat, pickup_lng)
    except Exception:
        pass

    pickup_code.attempts += 1
    pickup_code.last_attempt_at = now
    pickup_code.passenger_latitude = passenger_lat
    pickup_code.passenger_longitude = passenger_lng

    if not pickup_code.check_code(code):
        pickup_code.verification_result = 'wrong_code'
        pickup_code.save(update_fields=['attempts', 'last_attempt_at', 'verification_result', 'passenger_latitude', 'passenger_longitude'])
        remaining = max(pickup_code.max_attempts - pickup_code.attempts, 0)

        try:
            RideAuditEvent.objects.create(
                trip=booking.trip,
                booking=booking,
                actor=booking.passenger,
                event_type='PICKUP_CODE_VERIFY_WRONG',
                payload={
                    'booking_id': booking.id,
                    'passenger_id': passenger_id,
                    'attempts': pickup_code.attempts,
                    'remaining_attempts': remaining,
                    'timestamp': now.isoformat(),
                },
            )
        except Exception:
            pass

        return JsonResponse({'success': False, 'error': 'Incorrect code', 'remaining_attempts': remaining}, status=400)

    pickup_code.status = 'VERIFIED'
    pickup_code.verification_result = 'verified'
    pickup_code.save(update_fields=['attempts', 'last_attempt_at', 'status', 'verification_result', 'passenger_latitude', 'passenger_longitude'])

    booking.pickup_verified_at = now
    booking.updated_at = now
    booking.save(update_fields=['pickup_verified_at', 'updated_at'])

    try:
        RideAuditEvent.objects.create(
            trip=booking.trip,
            booking=booking,
            actor=booking.passenger,
            event_type='PICKUP_CODE_VERIFIED',
            payload={
                'booking_id': booking.id,
                'passenger_id': passenger_id,
                'timestamp': now.isoformat(),
            },
        )
    except Exception:
        pass

    trip = booking.trip
    payload_driver = {
        'user_id': str(trip.driver_id),
        'driver_id': str(trip.driver_id),
        'title': 'Passenger verified pickup',
        'body': 'Pickup code verified successfully.',
        'data': {
            'type': 'pickup_code_verified',
            'trip_id': str(trip.trip_id),
            'booking_id': str(booking.id),
        },
    }
    payload_passenger = {
        'user_id': str(booking.passenger_id),
        'driver_id': str(trip.driver_id),
        'title': 'Pickup verified',
        'body': 'Your pickup code was verified.',
        'data': {
            'type': 'pickup_code_verified',
            'trip_id': str(trip.trip_id),
            'booking_id': str(booking.id),
        },
    }
    try:
        send_ride_notification_async(payload_driver)
        send_ride_notification_async(payload_passenger)
    except Exception:
        pass

    try:
        passenger_name = getattr(getattr(booking, 'passenger', None), 'name', None) or 'Passenger'
        drop_name = getattr(getattr(booking, 'to_stop', None), 'stop_name', None) or 'Drop-off'
        payload_task = {
            'user_id': str(trip.driver_id),
            'driver_id': str(trip.driver_id),
            'title': 'Drop passenger',
            'body': f'You have to drop {passenger_name} at {drop_name}.',
            'data': {
                'type': 'driver_task_dropoff',
                'trip_id': str(trip.trip_id),
                'booking_id': str(booking.id),
                'passenger_name': str(passenger_name),
                'dropoff_stop_name': str(drop_name),
            },
        }
        send_ride_notification_async(payload_task)
    except Exception:
        pass

    return JsonResponse({'success': True, 'booking_id': booking.id, 'trip_id': trip.trip_id})


def compute_driver_reminder_time(trip: Trip):
    """Return timezone-aware datetime when driver reminder should fire (T-10m)."""
    trip_naive = timezone.datetime.combine(trip.trip_date, trip.departure_time)
    trip_dt = timezone.make_aware(trip_naive)
    trigger_at = trip_dt - timezone.timedelta(minutes=10)
    print(
        f"[compute_driver_reminder_time] trip_id={trip.trip_id} "
        f"trip_date={trip.trip_date} departure_time={trip.departure_time} "
        f"trip_dt={trip_dt.isoformat()} trigger_at={trigger_at.isoformat()}"
    )
    return trigger_at


def compute_passenger_reminder_time(trip: Trip, from_stop_order: int):
    """Compute passenger pickup ETA minus 10 minutes.

    Priority:
    1) Sum TripStopBreakdown.duration_minutes up to the pickup stop
    2) Fallback to RouteStop.estimated_time_from_start
    3) Fallback to trip departure (same as driver reminder)
    """
    trip_dt = timezone.make_aware(
        timezone.datetime.combine(trip.trip_date, trip.departure_time)
    )

    try:
        segments = (
            TripStopBreakdown.objects
            .filter(trip=trip)
            .order_by('from_stop_order', 'to_stop_order')
        )
        total_minutes = 0
        for seg in segments:
            total_minutes += seg.duration_minutes or 0
            if seg.to_stop_order >= from_stop_order:
                break
        if total_minutes > 0:
            pickup_eta = trip_dt + timezone.timedelta(minutes=total_minutes)
            trigger_at = pickup_eta - timezone.timedelta(minutes=10)
            print(
                f"[compute_passenger_reminder_time] trip_id={trip.trip_id} from_stop_order={from_stop_order} "
                f"via TripStopBreakdown total_minutes={total_minutes} "
                f"pickup_eta={pickup_eta.isoformat()} trigger_at={trigger_at.isoformat()}"
            )
            return trigger_at
    except Exception:
        pass

    try:
        stop = RouteStop.objects.get(route=trip.route, stop_order=from_stop_order)
        if getattr(stop, 'estimated_time_from_start', None) is not None:
            pickup_eta = trip_dt + timezone.timedelta(minutes=stop.estimated_time_from_start)
            trigger_at = pickup_eta - timezone.timedelta(minutes=10)
            print(
                f"[compute_passenger_reminder_time] trip_id={trip.trip_id} from_stop_order={from_stop_order} "
                f"via RouteStop est_minutes={stop.estimated_time_from_start} "
                f"pickup_eta={pickup_eta.isoformat()} trigger_at={trigger_at.isoformat()}"
            )
            return trigger_at
    except RouteStop.DoesNotExist:
        pass

    return compute_driver_reminder_time(trip)


# NOTE:
# The 10-minute pre-ride reminder notifications are not sent automatically.
# A background scheduler or management command must:
#   1) Call build_pre_ride_reminder_jobs_for_trip(trip) to get trigger times
#   2) At those times, call fire_pre_ride_reminder_notifications(trip)
# This ensures the driver and passengers receive the reminders at T-10 minutes.

def build_pre_ride_reminder_jobs_for_trip(trip: Trip):
    """Build logical reminder jobs for driver + passengers.

    This does not persist anything; it simply returns a dict the background
    scheduler/worker can use to enqueue jobs in its own queue.
    """
    now = timezone.now()

    driver_trigger_at = compute_driver_reminder_time(trip)

    passenger_jobs = []
    bookings = (
        Booking.objects
        .filter(trip=trip, booking_status='CONFIRMED')
        .select_related('from_stop', 'passenger')
    )
    for booking in bookings:
        from_order = getattr(booking.from_stop, 'stop_order', None)
        if from_order is None:
            print(
                f"[build_pre_ride_reminder_jobs_for_trip] trip_id={trip.trip_id} "
                f"booking_id={booking.id} has no from_stop_order, skipping passenger job"
            )
            continue
        trigger_at = compute_passenger_reminder_time(trip, from_order)
        passenger_jobs.append({
            'booking_id': booking.id,
            'passenger_id': booking.passenger.id,
            'trigger_at': trigger_at.isoformat(),
        })

    print(
        f"[build_pre_ride_reminder_jobs_for_trip] trip_id={trip.trip_id} "
        f"driver_trigger_at={driver_trigger_at.isoformat()} "
        f"passenger_jobs_count={len(passenger_jobs)}"
    )

    return {
        'now': now.isoformat(),
        'trip_id': trip.trip_id,
        'driver': {
            'driver_id': trip.driver_id,
            'trigger_at': driver_trigger_at.isoformat(),
        },
        'passengers': passenger_jobs,
    }


def fire_pre_ride_reminder_notifications(trip: Trip):
    """Send push + in-app notifications for driver and all confirmed passengers.

    This is meant to be called by the background scheduler at the time a
    reminder is due (driver at T-10 from departure; passenger at T-10 from
    their pickup ETA). It uses the existing Supabase Edge Function helper.
    """
    # Driver notification
    try:
        driver_payload = {
            'user_id': str(trip.driver_id),
            'driver_id': str(trip.driver_id),
            'title': 'Upcoming trip reminder',
            'body': 'Your LetsGo trip is starting in about 10 minutes. Please get ready to start the ride and make sure your location and internet are ON.',
            'data': {
                'type': 'pre_ride_reminder_driver',
                'trip_id': str(trip.trip_id),
            },
        }
        print(
            f"[fire_pre_ride_reminder_notifications] Sending DRIVER reminder: "
            f"trip_id={trip.trip_id} driver_id={trip.driver_id} payload={driver_payload}"
        )
        send_ride_notification_async(driver_payload)
    except Exception as e:
        print('[fire_pre_ride_reminder_notifications][driver][ERROR]:', e)

    # Passenger notifications
    bookings = (
        Booking.objects
        .filter(trip=trip, booking_status='CONFIRMED')
        .select_related('passenger')
    )
    for booking in bookings:
        try:
            passenger_payload = {
                'user_id': str(booking.passenger.id),
                'driver_id': str(trip.driver_id),
                'title': 'Pickup reminder',
                'body': 'Your LetsGo ride will pick you up in about 10 minutes near your selected pickup location. Please be ready outside and keep your location and internet ON.',
                'data': {
                    'type': 'pre_ride_reminder_passenger',
                    'trip_id': str(trip.trip_id),
                    'booking_id': str(booking.id),
                },
            }
            print(
                f"[fire_pre_ride_reminder_notifications] Sending PASSENGER reminder: "
                f"trip_id={trip.trip_id} booking_id={booking.id} passenger_id={booking.passenger.id} "
                f"payload={passenger_payload}"
            )
            send_ride_notification_async(passenger_payload)
        except Exception as e:
            print('[fire_pre_ride_reminder_notifications][passenger][ERROR]:', e)

