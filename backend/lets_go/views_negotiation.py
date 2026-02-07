from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from datetime import datetime
import json
import random
import time as pytime
from django.db import connection, transaction
from django.db.models import F
from django.db.utils import OperationalError, DatabaseError

from .models import UsersData, Trip, RouteStop, Booking, BlockedUser
from .views_notifications import send_ride_notification_async
from .utils.verification_guard import verification_block_response, ride_booking_block_response


def _to_int_pkr(value, default=None):
    if value is None:
        return default
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


# ================= Passenger request creation (bargaining) =================

@csrf_exempt
def handle_ride_booking_request(request, trip_id):
    """Handle ride booking requests with bargaining functionality"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)

            # Extract booking data
            passenger_id = data.get('passenger_id')
            from_stop_order = data.get('from_stop_order')
            to_stop_order = data.get('to_stop_order')
            male_seats = _to_int_pkr(data.get('male_seats'), default=0) or 0
            female_seats = _to_int_pkr(data.get('female_seats'), default=0) or 0
            split_total = int(male_seats) + int(female_seats)
            if split_total > 0:
                number_of_seats = int(split_total)
            else:
                number_of_seats = int(data.get('number_of_seats', 1) or 1)
            passenger_gender = data.get('passenger_gender', 'male')
            special_requests = data.get('special_requests', '')
            original_fare = data.get('original_fare')
            proposed_fare = data.get('proposed_fare')
            final_fare = data.get('final_fare')
            is_negotiated = data.get('is_negotiated', False)

            # Get trip
            try:
                trip = Trip.objects.get(trip_id=trip_id)
            except Trip.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': 'Trip not found'
                }, status=404)

            # Get passenger
            try:
                passenger = UsersData.objects.get(id=passenger_id)
            except UsersData.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': 'Passenger not found'
                }, status=404)

            # Block checks (per-trip and persistent)
            try:
                if Booking.objects.filter(trip_id=trip.id, passenger_id=passenger.id, blocked=True).only('id').exists():
                    return JsonResponse({'success': False, 'error': 'You are blocked from requesting this ride.'}, status=403)
            except Exception:
                pass
            try:
                if BlockedUser.objects.filter(blocker_id=trip.driver_id, blocked_user_id=passenger.id).only('id').exists():
                    return JsonResponse({'success': False, 'error': 'You are blocked by this driver.'}, status=403)
            except Exception:
                pass

            blocked = verification_block_response(passenger.id)
            if blocked is not None:
                return blocked

            # Validate stops
            try:
                from_stop = RouteStop.objects.get(route=trip.route, stop_order=from_stop_order)
                to_stop = RouteStop.objects.get(route=trip.route, stop_order=to_stop_order)
            except RouteStop.DoesNotExist:
                return JsonResponse({
                    'success': False,
                    'error': 'Invalid stop selection'
                }, status=400)

            if trip.available_seats < number_of_seats:
                return JsonResponse({
                    'success': False,
                    'error': f'Only {trip.available_seats} seats available'
                }, status=400)

            # Check gender preference
            trip_pref = (getattr(trip, 'gender_preference', None) or 'Any').strip().lower()
            if split_total <= 0:
                # Legacy payload: map all seats to passenger's gender
                pg = (getattr(passenger, 'gender', None) or passenger_gender or '').strip().lower()
                if pg == 'female':
                    female_seats = int(number_of_seats)
                    male_seats = 0
                else:
                    male_seats = int(number_of_seats)
                    female_seats = 0

            if trip_pref in ('male', 'female'):
                if trip_pref == 'male' and int(female_seats or 0) > 0:
                    return JsonResponse({
                        'success': False,
                        'error': 'This trip is for Male passengers only'
                    }, status=400)
                if trip_pref == 'female' and int(male_seats or 0) > 0:
                    return JsonResponse({
                        'success': False,
                        'error': 'This trip is for Female passengers only'
                    }, status=400)

            # Use fares provided by the client; no backend fare matrix calculation
            try:
                # IMPORTANT: Fare fields are treated as PER-SEAT amounts.
                # total_fare is always computed as (final_fare_per_seat * number_of_seats).
                if original_fare is None:
                    original_fare = int(trip.base_fare or 0)
                else:
                    original_fare = _to_int_pkr(original_fare, default=0)

                if proposed_fare is not None:
                    proposed_fare = _to_int_pkr(proposed_fare, default=None)

                # Allow client to send an explicit final per-seat fare, otherwise derive it.
                if final_fare is not None:
                    final_fare = _to_int_pkr(final_fare, default=None)
                else:
                    if is_negotiated and proposed_fare is not None:
                        final_fare = int(proposed_fare)
                    else:
                        final_fare = int(original_fare)
            except Exception as e:
                print(f"[handle_ride_booking_request] Error normalizing fare fields: {e}")
                original_fare = int(trip.base_fare or 0)
                final_fare = int(original_fare)

            total_fare = int(final_fare or 0) * int(number_of_seats or 0)

            # IMPORTANT:
            # negotiated_fare represents the *driver's* latest counter (or final accepted fare),
            # and should NOT be initialized to the passenger's proposed fare. Otherwise the
            # passenger UI incorrectly shows an initial "Driver Counter" equal to the
            # passenger's request price.
            negotiated_fare_to_store = None
            if not is_negotiated:
                negotiated_fare_to_store = final_fare

            with transaction.atomic():
                trip_locked = (
                    Trip.objects
                    .select_for_update()
                    .only('id', 'trip_id', 'available_seats', 'gender_preference', 'driver_id', 'is_negotiable')
                    .get(id=trip.id)
                )

                if trip_locked.available_seats < number_of_seats:
                    return JsonResponse({
                        'success': False,
                        'error': f'Only {trip_locked.available_seats} seats available'
                    }, status=409)

                # Create booking with bargaining information
                booking = Booking.objects.create(
                    booking_id=f"B{random.randint(100, 999)}-{datetime.now().strftime('%Y-%m-%d-%H:%M')}-{passenger_id}",
                    trip=trip,
                    passenger=passenger,
                    from_stop=from_stop,
                    to_stop=to_stop,
                    number_of_seats=number_of_seats,
                    male_seats=male_seats,
                    female_seats=female_seats,
                    total_fare=total_fare,
                    original_fare=original_fare,
                    passenger_offer=proposed_fare,
                    negotiated_fare=negotiated_fare_to_store,
                    booking_status='PENDING',
                    bargaining_status='PENDING' if is_negotiated else 'NO_NEGOTIATION',
                    negotiation_notes=special_requests,
                    seats_locked=True,
                )

                Trip.objects.filter(id=trip_locked.id).update(
                    available_seats=F('available_seats') - number_of_seats
                )

            # Fire-and-forget notification to the driver via Supabase Edge Function
            try:
                driver_user_id = getattr(trip, 'driver_id', None)
                print(f"[handle_ride_booking_request] driver_user_id={driver_user_id}")
                if driver_user_id:
                    payload = {
                        'user_id': str(driver_user_id),
                        'driver_id': str(driver_user_id),
                        'title': 'New ride request',
                        'body': f'Passenger requested {number_of_seats} seat(s).',
                        'data': {
                            'type': 'ride_request',
                            'trip_id': str(trip.trip_id),
                            'booking_id': str(booking.id),
                            'seats': str(number_of_seats),
                            'from_stop_name': str(getattr(from_stop, 'stop_name', '') or ''),
                            'to_stop_name': str(getattr(to_stop, 'stop_name', '') or ''),
                            'from_stop_order': str(getattr(from_stop, 'stop_order', '') or ''),
                            'to_stop_order': str(getattr(to_stop, 'stop_order', '') or ''),
                            'sender_id': str(passenger.id),
                            'sender_name': str(passenger.name or ''),
                            'sender_role': 'passenger',
                            'sender_photo_url': str(getattr(passenger, 'profile_photo_url', '') or ''),
                        },
                    }
                    print(f"[handle_ride_booking_request] Calling send_ride_notification_async with payload={payload}")
                    send_ride_notification_async(payload)
            except Exception as e:
                print(f"[handle_ride_booking_request][notify_error]: {e}")

            # Add to bargaining history if negotiated
            if is_negotiated:
                bargaining_entry = {
                    'timestamp': datetime.now().isoformat(),
                    'passenger_id': passenger_id,
                    'passenger_name': passenger.name,
                    'original_fare': int(original_fare) if original_fare is not None else 0,
                    'proposed_fare': int(proposed_fare) if proposed_fare is not None else None,
                    'status': 'PENDING'
                }

                if not trip.bargaining_history:
                    trip.bargaining_history = []
                trip.bargaining_history.append(bargaining_entry)
                trip.save()

            return JsonResponse({
                'success': True,
                'message': 'Ride booking request submitted successfully',
                'booking_id': booking.booking_id,
                'booking_pk': booking.id,
                'bargaining_status': booking.bargaining_status,
                'total_fare': int(booking.total_fare) if booking.total_fare is not None else 0
            }, status=201)

        except json.JSONDecodeError:
            return JsonResponse({
                'success': False,
                'error': 'Invalid JSON data'
            }, status=400)
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Failed to submit booking request: {str(e)}'
            }, status=500)

    return JsonResponse({
        'success': False,
        'error': 'Only POST method allowed'
    }, status=405)


# ================= Driver request management & negotiation =================

@csrf_exempt
def list_pending_requests(request, trip_id):
    """Return all pending booking requests for a trip (driver-facing)."""
    if request.method != 'GET':
        return JsonResponse({'success': False, 'error': 'Only GET allowed'}, status=405)
    try:
        t0 = pytime.time()
        print(f"[list_pending_requests] START trip_id={trip_id}")
        # Ensure DB connection is healthy (mitigate SSL EOF)
        try:
            connection.close_if_unusable_or_obsolete()
        except Exception:
            pass

        # Fetch trip (simple path)
        t1 = pytime.time()
        trip_row = (
            Trip.objects
            .filter(trip_id=trip_id)
            .values_list('id', 'driver_id')
            .first()
        )
        if not trip_row:
            return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)
        trip_pk, trip_driver_id = trip_row
        print(f"[list_pending_requests] Trip lookup took {(pytime.time()-t1)*1000:.1f}ms (pk={trip_pk})")

        # Fetch requests (pending + finalized) so driver can still open and review negotiation history.
        t2 = pytime.time()
        requests_qs = (
            Booking.objects
            .filter(trip_id=trip_pk)
            .select_related('passenger', 'from_stop', 'to_stop')
            .only(
                'id', 'number_of_seats', 'male_seats', 'female_seats', 'booking_status', 'bargaining_status', 'passenger_offer', 'booked_at',
                'passenger_id', 'passenger__name', 'passenger__gender', 'passenger__passenger_rating',
                'from_stop__stop_name', 'to_stop__stop_name',
            )
            .order_by('-booked_at')[:50]
        )
        print(f"[list_pending_requests] Requests query took {(pytime.time()-t2)*1000:.1f}ms, count={requests_qs.count()}")

        # Build minimal payload for list
        t3 = pytime.time()
        items = []
        for b in requests_qs:
            items.append({
                'booking_id': b.id,
                'passenger_name': b.passenger.name if b.passenger_id else 'Passenger',
                'passenger_gender': str(b.passenger.gender) if b.passenger_id else None,
                'passenger_rating': float(getattr(b.passenger, 'passenger_rating', 0.0)) if b.passenger_id and getattr(b.passenger, 'passenger_rating', None) is not None else None,
                # Use Supabase-hosted profile photo URL if available
                'passenger_photo_url': (getattr(b.passenger, 'profile_photo_url', None) if b.passenger_id else None),
                'number_of_seats': int(b.number_of_seats) if b.number_of_seats else 0,
                'male_seats': int(getattr(b, 'male_seats', 0) or 0),
                'female_seats': int(getattr(b, 'female_seats', 0) or 0),
                'from_stop_name': b.from_stop.stop_name if b.from_stop_id else None,
                'to_stop_name': b.to_stop.stop_name if b.to_stop_id else None,
                'passenger_offer_per_seat': int(b.passenger_offer) if b.passenger_offer is not None else None,
                'booking_status': str(b.booking_status) if getattr(b, 'booking_status', None) else None,
                'bargaining_status': str(b.bargaining_status) if b.bargaining_status else 'PENDING',
                'requested_at': b.booked_at.isoformat() if getattr(b, 'booked_at', None) else None,
            })
        print(f"[list_pending_requests] Serialize took {(pytime.time()-t3)*1000:.1f}ms, total elapsed {(pytime.time()-t0)*1000:.1f}ms")
        return JsonResponse({'success': True, 'requests': items, 'pending_requests': items})
    except Trip.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)
    except OperationalError as e:
        # Attempt one reconnect and retry
        try:
            print('[list_pending_requests] OperationalError, attempting reconnect:', e)
            connection.close()
            connection.connect()
            # Retry logic
            trip_row = (
                Trip.objects
                .filter(trip_id=trip_id)
                .values_list('id', 'driver_id')
                .first()
            )
            if not trip_row:
                return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)
            trip_pk, _ = trip_row
            pending = (
                Booking.objects.filter(trip_id=trip_pk, booking_status='PENDING')
                .select_related('passenger', 'from_stop', 'to_stop')
                .order_by('-booked_at')
            )
            items = []
            for b in pending:
                items.append({
                    'booking_id': b.id,
                    'passenger_name': b.passenger.name if b.passenger_id else 'Passenger',
                    'passenger_gender': str(b.passenger.gender) if b.passenger_id else None,
                    'number_of_seats': int(b.number_of_seats) if b.number_of_seats else 0,
                    'from_stop_name': b.from_stop.stop_name if b.from_stop_id else None,
                    'to_stop_name': b.to_stop.stop_name if b.to_stop_id else None,
                    'passenger_offer_per_seat': int(b.passenger_offer) if b.passenger_offer is not None else None,
                    'bargaining_status': str(b.bargaining_status) if b.bargaining_status else 'PENDING',
                })
            return JsonResponse({'success': True, 'pending_requests': items})
        except Exception as ex:
            print('[list_pending_requests][RETRY_FAIL]:', ex)
            return JsonResponse({'success': False, 'error': 'Database connection error, please retry'}, status=500)
    except Exception as e:
        print('[list_pending_requests][ERROR]:', e)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def _serialize_booking_detail(b: Booking):
    return {
        'booking_id': b.id,
        'trip_id': b.trip.trip_id if b.trip_id else None,
        'passenger_id': b.passenger_id,
        'passenger_name': b.passenger.name if b.passenger_id else 'Passenger',
        'passenger_gender': str(b.passenger.gender) if b.passenger_id else None,
        'passenger_rating': float(b.passenger.passenger_rating) if (b.passenger_id and b.passenger.passenger_rating is not None) else None,
        'number_of_seats': int(b.number_of_seats) if b.number_of_seats else 0,
        'male_seats': int(getattr(b, 'male_seats', 0) or 0),
        'female_seats': int(getattr(b, 'female_seats', 0) or 0),
        'from_stop_id': getattr(b, 'from_stop_id', None),
        'from_stop_name': b.from_stop.stop_name if getattr(b, 'from_stop_id', None) else None,
        'to_stop_id': getattr(b, 'to_stop_id', None),
        'to_stop_name': b.to_stop.stop_name if getattr(b, 'to_stop_id', None) else None,
        'original_fare_per_seat': int(b.original_fare) if b.original_fare is not None else None,
        'negotiated_fare_per_seat': int(b.negotiated_fare) if b.negotiated_fare is not None else None,
        'passenger_offer_per_seat': int(b.passenger_offer) if b.passenger_offer is not None else None,
        'passenger_offer_total': int((b.passenger_offer or 0) * (b.number_of_seats or 0)) if b.passenger_offer is not None and b.number_of_seats else None,
        'passenger_message': b.negotiation_notes if getattr(b, 'negotiation_notes', None) else None,
        'bargaining_status': str(b.bargaining_status) if b.bargaining_status else None,
        'booking_status': str(b.booking_status),
        'requested_at': b.booked_at.isoformat() if b.booked_at else None,
    }


@csrf_exempt
def booking_request_details(request, trip_id, booking_id):
    """GET: Full details for a single booking request (driver detail view)."""
    if request.method != 'GET':
        return JsonResponse({'success': False, 'error': 'Only GET allowed'}, status=405)
    try:
        trip = Trip.objects.only('id', 'trip_id').get(trip_id=trip_id)
        b = (
            Booking.objects
            .select_related('trip', 'passenger', 'from_stop', 'to_stop')
            .get(id=booking_id, trip_id=trip.id)
        )
        return JsonResponse({'success': True, 'booking': _serialize_booking_detail(b)})
    except Trip.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)
    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)
    except Exception as e:
        import traceback
        print('[RESPOND_REQUEST][ERROR]', e)
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def respond_booking_request(request, trip_id, booking_id):
    """Driver responds to a booking request: accept/counter/reject."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Only POST allowed'}, status=405)
    try:
        t0 = pytime.time()
        print(f"[respond_booking_request] START trip_id={trip_id}, booking_id={booking_id}")
        data = json.loads(request.body or '{}')
        action = (data.get('action') or '').lower()
        driver_id = data.get('driver_id')
        counter_fare = data.get('counter_fare')
        reason = data.get('reason')
        print(f"[respond_booking_request] Parsed payload action={action}, driver_id={driver_id}, counter_fare={counter_fare}, reason={reason}")

        if not driver_id:
            return JsonResponse({'success': False, 'error': 'driver_id is required'}, status=400)
        if action not in ['accept', 'reject', 'counter', 'block', 'blacklist']:
            return JsonResponse({'success': False, 'error': 'Invalid action'}, status=400)

        t1 = pytime.time()
        try:
            # [respond_booking_request] Ensure DB connection is healthy before querying
            connection.close_if_unusable_or_obsolete()
        except Exception:
            pass
        trip = (
            Trip.objects
            .only('id', 'trip_id', 'driver_id', 'is_negotiable', 'available_seats', 'bargaining_history')
            .get(trip_id=trip_id)
        )
        print(f"[respond_booking_request] Loaded trip in {(pytime.time()-t1)*1000:.1f}ms, driver_id={trip.driver_id}")
        if trip.driver_id != int(driver_id):
            return JsonResponse({'success': False, 'error': 'Only the trip driver can respond'}, status=403)

        blocked = verification_block_response(int(driver_id))
        if blocked is not None:
            return blocked

        booking = (
            Booking.objects
            .select_related('trip', 'passenger')
            .only(
                'id', 'trip_id', 'passenger_id', 'number_of_seats',
                'booking_status', 'bargaining_status',
                'negotiated_fare', 'passenger_offer', 'total_fare',
                'driver_response'
            )
            .get(id=booking_id)
        )
        if booking.trip_id != trip.id:
            return JsonResponse({'success': False, 'error': 'Booking does not belong to this trip'}, status=400)

        if action == 'accept':
            t3 = pytime.time()
            if not getattr(booking, 'seats_locked', False):
                if trip.available_seats < booking.number_of_seats:
                    return JsonResponse({'success': False, 'error': 'Not enough seats available'}, status=409)
            # Safely determine final PER-SEAT fare, then compute total_fare = per_seat * seats
            final_per_seat = None
            if getattr(trip, 'is_negotiable', False):
                # Driver acceptance should accept the passenger's latest offer (fairness rule).
                # If passenger never proposed, fall back to driver's last counter.
                if booking.passenger_offer is not None:
                    final_per_seat = booking.passenger_offer
                elif booking.negotiated_fare is not None:
                    final_per_seat = booking.negotiated_fare
                booking.bargaining_status = 'ACCEPTED'

            if final_per_seat is None:
                # Fallback to original per-seat fare if present
                try:
                    final_per_seat = getattr(booking, 'original_fare', None)
                except Exception:
                    final_per_seat = None

            seats = booking.number_of_seats or 1
            try:
                final_total = int(final_per_seat) * int(seats) if final_per_seat is not None else None
            except Exception:
                final_total = None

            try:
                booking.negotiated_fare = int(final_per_seat) if final_per_seat is not None else booking.negotiated_fare
            except Exception:
                pass
            # Only set total_fare if the model has that field
            try:
                setattr(booking, 'total_fare', final_total)
            except Exception:
                pass
            booking.booking_status = 'CONFIRMED'
            booking.driver_response = reason
            booking.save()

            try:
                if not getattr(booking, 'seats_locked', False):
                    trip.available_seats -= booking.number_of_seats
                    trip.save(update_fields=['available_seats'])
                    booking.seats_locked = True
                    booking.save(update_fields=['seats_locked', 'updated_at'])
            except Exception:
                pass
            # store event
            try:
                hist = trip.bargaining_history or []
                hist.append({'action': 'driver_accept', 'passenger_id': booking.passenger_id, 'booking_id': booking.id, 'ts': timezone.now().isoformat(), 'accepted_fare_per_seat': int(final_per_seat) if final_per_seat is not None else None, 'accepted_fare_total': int(final_total) if final_total is not None else None})
                trip.bargaining_history = hist
                trip.save(update_fields=['bargaining_history'])
            except Exception:
                pass
            print(f"[respond_booking_request] ACCEPT branch completed in {(pytime.time()-t3)*1000:.1f}ms, final_per_seat={final_per_seat} final_total={final_total}")
            # Notify passenger of acceptance
            try:
                passenger_id = booking.passenger_id
                if passenger_id:
                    payload = {
                        'user_id': str(passenger_id),
                        'driver_id': str(trip.driver_id),
                        'title': 'Your request was accepted',
                        'body': 'Driver confirmed your booking.',
                        'data': {
                            'type': 'booking_update',
                            'action': 'driver_accept',
                            'trip_id': str(trip.trip_id),
                            'booking_id': str(booking.id),
                            'seats': str(getattr(booking, 'number_of_seats', '') or ''),
                            'from_stop_name': str(getattr(getattr(booking, 'from_stop', None), 'stop_name', '') or ''),
                            'to_stop_name': str(getattr(getattr(booking, 'to_stop', None), 'stop_name', '') or ''),
                            'from_stop_order': str(getattr(getattr(booking, 'from_stop', None), 'stop_order', '') or ''),
                            'to_stop_order': str(getattr(getattr(booking, 'to_stop', None), 'stop_order', '') or ''),
                            'sender_id': str(trip.driver_id),
                            'sender_name': str(getattr(trip, 'driver', None).name if getattr(trip, 'driver', None) else 'Driver'),
                            'sender_role': 'driver',
                            'sender_photo_url': str(getattr(getattr(trip, 'driver', None), 'profile_photo_url', '') or ''),
                        },
                    }
                    print(f"[respond_booking_request] Calling send_ride_notification_async with payload={payload}")
                    send_ride_notification_async(payload)
            except Exception as e:
                print('[respond_booking_request][notify_error][accept]:', e)
            print(f"[respond_booking_request] END action=accept total_elapsed={(pytime.time()-t0)*1000:.1f}ms")
            return JsonResponse({'success': True, 'message': 'Booking confirmed', 'booking': {
                'id': booking.id,
                'status': booking.booking_status,
                'bargaining_status': booking.bargaining_status,
                'total_fare': int(getattr(booking, 'total_fare', 0) or 0),
            }})
        elif action == 'reject':
            t3 = pytime.time()
            booking.bargaining_status = 'REJECTED'
            booking.booking_status = 'CANCELLED'
            booking.driver_response = reason
            booking.save()
            try:
                if getattr(booking, 'seats_locked', False):
                    Trip.objects.filter(id=trip.id).update(
                        available_seats=F('available_seats') + (booking.number_of_seats or 0)
                    )
                    booking.seats_locked = False
                    booking.save(update_fields=['seats_locked', 'updated_at'])
            except Exception:
                pass
            try:
                hist = trip.bargaining_history or []
                hist.append({'action': 'reject', 'passenger_id': booking.passenger_id, 'booking_id': booking.id, 'reason': reason, 'ts': timezone.now().isoformat()})
                trip.bargaining_history = hist
                trip.save(update_fields=['bargaining_history'])
            except Exception:
                pass
            print(f"[respond_booking_request] REJECT branch completed in {(pytime.time()-t3)*1000:.1f}ms")
            # Notify passenger of rejection
            try:
                passenger_id = booking.passenger_id
                if passenger_id:
                    payload = {
                        'user_id': str(passenger_id),
                        'driver_id': str(trip.driver_id),
                        'title': 'Your request was rejected',
                        'body': 'Driver rejected your booking request.',
                        'data': {
                            'type': 'booking_update',
                            'action': 'driver_reject',
                            'trip_id': str(trip.trip_id),
                            'booking_id': str(booking.id),
                            'seats': str(getattr(booking, 'number_of_seats', '') or ''),
                            'from_stop_name': str(getattr(getattr(booking, 'from_stop', None), 'stop_name', '') or ''),
                            'to_stop_name': str(getattr(getattr(booking, 'to_stop', None), 'stop_name', '') or ''),
                            'from_stop_order': str(getattr(getattr(booking, 'from_stop', None), 'stop_order', '') or ''),
                            'to_stop_order': str(getattr(getattr(booking, 'to_stop', None), 'stop_order', '') or ''),
                            'sender_id': str(trip.driver_id),
                            'sender_name': str(getattr(trip, 'driver', None).name if getattr(trip, 'driver', None) else 'Driver'),
                            'sender_role': 'driver',
                            'sender_photo_url': str(getattr(getattr(trip, 'driver', None), 'profile_photo_url', '') or ''),
                        },
                    }
                    print(f"[respond_booking_request] Calling send_ride_notification_async with payload={payload}")
                    send_ride_notification_async(payload)
            except Exception as e:
                print('[respond_booking_request][notify_error][reject]:', e)
            print(f"[respond_booking_request] END action=reject total_elapsed={(pytime.time()-t0)*1000:.1f}ms")
            return JsonResponse({'success': True, 'message': 'Booking rejected', 'booking': {
                'id': booking.id,
                'status': booking.booking_status,
                'bargaining_status': booking.bargaining_status,
            }})
        elif action == 'counter':  # counter
            if not getattr(trip, 'is_negotiable', False):
                return JsonResponse({'success': False, 'error': 'Trip is not negotiable'}, status=400)
            if counter_fare is None:
                return JsonResponse({'success': False, 'error': 'counter_fare is required for counter action'}, status=400)
            booking.negotiated_fare = _to_int_pkr(counter_fare, default=booking.negotiated_fare)
            booking.bargaining_status = 'COUNTER_OFFER'
            booking.driver_response = reason
            booking.save()
            try:
                hist = trip.bargaining_history or []
                hist.append({'action': 'driver_counter', 'passenger_id': booking.passenger_id, 'booking_id': booking.id, 'counter_fare': _to_int_pkr(counter_fare, default=None), 'reason': reason, 'ts': timezone.now().isoformat()})
                trip.bargaining_history = hist
                trip.save(update_fields=['bargaining_history'])
            except Exception:
                pass
            # Notify passenger of counter offer
            try:
                passenger_id = booking.passenger_id
                if passenger_id:
                    payload = {
                        'user_id': str(passenger_id),
                        'driver_id': str(trip.driver_id),
                        'title': 'You have a counter offer',
                        'body': f"Driver offered PKR {_to_int_pkr(counter_fare, default=0)} per seat.",
                        'data': {
                            'type': 'booking_update',
                            'action': 'driver_counter',
                            'trip_id': str(trip.trip_id),
                            'booking_id': str(booking.id),
                            'seats': str(getattr(booking, 'number_of_seats', '') or ''),
                            'counter_fare': str(_to_int_pkr(counter_fare, default=0)),
                            'from_stop_name': str(getattr(getattr(booking, 'from_stop', None), 'stop_name', '') or ''),
                            'to_stop_name': str(getattr(getattr(booking, 'to_stop', None), 'stop_name', '') or ''),
                            'from_stop_order': str(getattr(getattr(booking, 'from_stop', None), 'stop_order', '') or ''),
                            'to_stop_order': str(getattr(getattr(booking, 'to_stop', None), 'stop_order', '') or ''),
                            'sender_id': str(trip.driver_id),
                            'sender_name': str(getattr(trip, 'driver', None).name if getattr(trip, 'driver', None) else 'Driver'),
                            'sender_role': 'driver',
                            'sender_photo_url': str(getattr(getattr(trip, 'driver', None), 'profile_photo_url', '') or ''),
                        },
                    }
                    print(f"[respond_booking_request] Calling send_ride_notification_async with payload={payload}")
                    send_ride_notification_async(payload)
            except Exception as e:
                print('[respond_booking_request][notify_error][counter]:', e)
            return JsonResponse({'success': True, 'message': 'Counter offer sent', 'booking': {
                'id': booking.id,
                'bargaining_status': booking.bargaining_status,
                'negotiated_fare': int(booking.negotiated_fare) if booking.negotiated_fare is not None else None,
            }})
        elif action == 'block':
            # Block passenger for this ride only
            booking.bargaining_status = 'BLOCKED'
            booking.booking_status = 'CANCELLED'
            booking.driver_response = reason
            try:
                booking.blocked = True
            except Exception:
                pass
            booking.save(update_fields=['bargaining_status', 'booking_status', 'driver_response', 'blocked'])
            try:
                if getattr(booking, 'seats_locked', False):
                    Trip.objects.filter(id=trip.id).update(
                        available_seats=F('available_seats') + (booking.number_of_seats or 0)
                    )
                    booking.seats_locked = False
                    booking.save(update_fields=['seats_locked', 'updated_at'])
            except Exception:
                pass
            try:
                hist = trip.bargaining_history or []
                hist.append({'action': 'block', 'passenger_id': booking.passenger_id, 'booking_id': booking.id, 'reason': reason, 'ts': timezone.now().isoformat()})
                trip.bargaining_history = hist
                trip.save(update_fields=['bargaining_history'])
            except Exception:
                pass
            return JsonResponse({'success': True, 'message': 'Passenger blocked for this ride', 'booking': {
                'id': booking.id,
                'status': booking.booking_status,
                'bargaining_status': booking.bargaining_status,
            }})
        elif action == 'blacklist':
            # Mark blacklist event (system-wide enforcement requires separate model)
            booking.bargaining_status = 'BLOCKED'
            booking.booking_status = 'CANCELLED'
            booking.driver_response = reason
            try:
                booking.blocked = True
            except Exception:
                pass
            booking.save(update_fields=['bargaining_status', 'booking_status', 'driver_response', 'blocked'])
            try:
                BlockedUser.objects.get_or_create(
                    blocker_id=int(driver_id),
                    blocked_user_id=int(booking.passenger_id),
                    defaults={'reason': reason or None},
                )
            except Exception:
                pass
            try:
                if getattr(booking, 'seats_locked', False):
                    Trip.objects.filter(id=trip.id).update(
                        available_seats=F('available_seats') + (booking.number_of_seats or 0)
                    )
                    booking.seats_locked = False
                    booking.save(update_fields=['seats_locked', 'updated_at'])
            except Exception:
                pass
            try:
                hist = trip.bargaining_history or []
                hist.append({'action': 'blacklist', 'passenger_id': booking.passenger_id, 'booking_id': booking.id, 'reason': reason, 'ts': timezone.now().isoformat()})
                trip.bargaining_history = hist
                trip.save(update_fields=['bargaining_history'])
            except Exception:
                pass
            return JsonResponse({'success': True, 'message': 'Passenger added to blacklist', 'booking': {
                'id': booking.id,
                'status': booking.booking_status,
                'bargaining_status': booking.bargaining_status,
            }})
        else:
            return JsonResponse({'success': False, 'error': 'Unsupported action'}, status=400)
    except Trip.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)
    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def unblock_passenger_for_trip(request, trip_id, passenger_id):
    """Driver unblocks a passenger for this trip so they can request again."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Only POST allowed'}, status=405)
    try:
        data = json.loads(request.body or '{}')
        driver_id = data.get('driver_id')
        if not driver_id:
            return JsonResponse({'success': False, 'error': 'driver_id is required'}, status=400)

        trip = Trip.objects.only('id', 'trip_id', 'driver_id').get(trip_id=trip_id)
        if trip.driver_id != int(driver_id):
            return JsonResponse({'success': False, 'error': 'Only the trip driver can unblock'}, status=403)

        Booking.objects.filter(trip_id=trip.id, passenger_id=int(passenger_id)).update(blocked=False)
        return JsonResponse({'success': True, 'message': 'Passenger unblocked for this ride'})
    except Trip.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ================= Passenger responds to negotiation =================

@csrf_exempt
def passenger_respond_booking(request, trip_id, booking_id):
    """Passenger responds to a driver's decision: accept/counter/withdraw.
    - accept: confirm booking (if driver offered/countered) and set CONFIRMED
    - counter: set passenger_offer and keep PENDING; bargaining_status=PASSENGER_COUNTER
    - withdraw: cancel booking; booking_status=CANCELLED; bargaining_status=WITHDRAWN
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Only POST allowed'}, status=405)
    try:
        t0 = pytime.time()
        print(f"[passenger_respond_booking] START trip_id={trip_id}, booking_id={booking_id}")
        data = json.loads(request.body or '{}')
        action = (data.get('action') or '').lower()
        passenger_id = data.get('passenger_id')
        counter_fare = data.get('counter_fare')
        note = data.get('note') or data.get('reason')
        print(f"[passenger_respond_booking] Parsed payload action={action}, passenger_id={passenger_id}, counter_fare={counter_fare}, note={note}")

        if not passenger_id:
            return JsonResponse({'success': False, 'error': 'passenger_id is required'}, status=400)
        if action not in ['accept', 'counter', 'withdraw']:
            return JsonResponse({'success': False, 'error': 'Invalid action'}, status=400)

        t1 = pytime.time()
        try:
            # [passenger_respond_booking] Ensure DB connection is healthy before querying
            connection.close_if_unusable_or_obsolete()
        except Exception:
            pass
        trip = Trip.objects.only('id', 'trip_id', 'driver_id', 'bargaining_history').get(trip_id=trip_id)
        booking = Booking.objects.select_related('trip', 'passenger').only(
            'id', 'trip_id', 'passenger_id', 'number_of_seats', 'booking_status',
            'bargaining_status', 'negotiated_fare', 'passenger_offer', 'original_fare', 'total_fare'
        ).get(id=booking_id)
        print(f"[passenger_respond_booking] Loaded trip and booking in {(pytime.time()-t1)*1000:.1f}ms; booking.passenger_id={booking.passenger_id}")
        if booking.trip_id != trip.id:
            return JsonResponse({'success': False, 'error': 'Booking does not belong to this trip'}, status=400)
        if int(passenger_id) != int(booking.passenger_id or 0):
            return JsonResponse({'success': False, 'error': 'Only the passenger can respond'}, status=403)

        blocked = verification_block_response(int(passenger_id))
        if blocked is not None:
            return blocked

        if action == 'accept':
            if (getattr(booking, 'bargaining_status', None) or '').upper() != 'COUNTER_OFFER':
                return JsonResponse({'success': False, 'error': 'No driver counter offer to accept yet'}, status=400)
            t2 = pytime.time()
            t = Trip.objects.only('id', 'available_seats').get(id=booking.trip_id)
            print(f"[passenger_respond_booking] ACCEPT branch: loaded trip for seat check in {(pytime.time()-t2)*1000:.1f}ms, available_seats={t.available_seats}")
            if t.available_seats < (booking.number_of_seats or 1):
                return JsonResponse({'success': False, 'error': 'Not enough seats available'}, status=409)
            # Determine final PER-SEAT fare, then compute total_fare = per_seat * seats
            final_per_seat = None
            # Passenger acceptance should accept the driver's latest offer.
            # If driver never countered, fall back to the original fare.
            if getattr(booking, 'negotiated_fare', None) is not None:
                final_per_seat = booking.negotiated_fare
            elif getattr(booking, 'original_fare', None) is not None:
                final_per_seat = booking.original_fare

            seats = booking.number_of_seats or 1
            try:
                final_total = int(final_per_seat) * int(seats) if final_per_seat is not None else None
            except Exception:
                final_total = None

            try:
                setattr(booking, 'total_fare', final_total)
            except Exception:
                pass
            try:
                booking.negotiated_fare = int(final_per_seat) if final_per_seat is not None else booking.negotiated_fare
            except Exception:
                pass
            booking.booking_status = 'CONFIRMED'
            booking.bargaining_status = 'ACCEPTED'
            setattr(booking, 'passenger_response', note)
            booking.save()
            t.available_seats -= (booking.number_of_seats or 1)
            t.save(update_fields=['available_seats'])
            print(f"[passenger_respond_booking] ACCEPT branch: updated booking and seats in {(pytime.time()-t2)*1000:.1f}ms, final_per_seat={final_per_seat} final_total={final_total}")
            # Store event in trip bargaining history
            try:
                hist = trip.bargaining_history or []
                hist.append({
                    'action': 'passenger_accept',
                    'passenger_id': booking.passenger_id,
                    'booking_id': booking.id,
                    'accepted_fare_per_seat': int(final_per_seat) if final_per_seat is not None else None,
                    'accepted_fare_total': int(final_total) if final_total is not None else None,
                    'note': note,
                    'ts': timezone.now().isoformat(),
                })
                trip.bargaining_history = hist
                trip.save(update_fields=['bargaining_history'])
            except Exception as e:
                print('[passenger_respond_booking][history_error][accept]:', e)
            # Notify driver that passenger accepted
            try:
                driver_user_id = getattr(trip, 'driver_id', None)
                if driver_user_id:
                    payload = {
                        'user_id': str(driver_user_id),
                        'driver_id': str(driver_user_id),
                        'title': 'Passenger confirmed booking',
                        'body': f'Passenger confirmed booking for {booking.number_of_seats or 1} seat(s).',
                        'data': {
                            'type': 'booking_update',
                            'action': 'passenger_accept',
                            'trip_id': str(trip.trip_id),
                            'booking_id': str(booking.id),
                            'seats': str(getattr(booking, 'number_of_seats', '') or ''),
                            'from_stop_name': str(getattr(getattr(booking, 'from_stop', None), 'stop_name', '') or ''),
                            'to_stop_name': str(getattr(getattr(booking, 'to_stop', None), 'stop_name', '') or ''),
                            'from_stop_order': str(getattr(getattr(booking, 'from_stop', None), 'stop_order', '') or ''),
                            'to_stop_order': str(getattr(getattr(booking, 'to_stop', None), 'stop_order', '') or ''),
                            'sender_id': str(booking.passenger_id),
                            'sender_name': str(getattr(booking, 'passenger', None).name if getattr(booking, 'passenger', None) else 'Passenger'),
                            'sender_role': 'passenger',
                            'sender_photo_url': str(getattr(getattr(booking, 'passenger', None), 'profile_photo_url', '') or ''),
                        },
                    }
                    print(f"[passenger_respond_booking] Calling send_ride_notification_async with payload={payload}")
                    send_ride_notification_async(payload)
            except Exception as e:
                print('[passenger_respond_booking][notify_error][accept]:', e)
            print(f"[passenger_respond_booking] END action=accept total_elapsed={(pytime.time()-t0)*1000:.1f}ms")
            return JsonResponse({'success': True, 'message': 'Booking confirmed', 'booking': {
                'id': booking.id,
                'status': booking.booking_status,
                'bargaining_status': booking.bargaining_status,
                'total_fare': int(getattr(booking, 'total_fare', 0) or 0),
            }})
        elif action == 'counter':
            if counter_fare is None:
                return JsonResponse({'success': False, 'error': 'counter_fare is required for counter action'}, status=400)
            booking.passenger_offer = _to_int_pkr(counter_fare, default=booking.passenger_offer)
            booking.bargaining_status = 'PASSENGER_COUNTER'
            setattr(booking, 'passenger_response', note)
            booking.booking_status = 'PENDING'
            booking.save()
            try:
                hist = trip.bargaining_history or []
                hist.append({
                    'action': 'passenger_counter',
                    'passenger_id': booking.passenger_id,
                    'booking_id': booking.id,
                    'counter_fare': _to_int_pkr(counter_fare, default=None),
                    'note': note,
                    'ts': timezone.now().isoformat(),
                })
                trip.bargaining_history = hist
                trip.save(update_fields=['bargaining_history'])
            except Exception as e:
                print('[passenger_respond_booking][history_error][counter]:', e)
            # Notify driver about passenger counter offer
            try:
                driver_user_id = getattr(trip, 'driver_id', None)
                if driver_user_id:
                    payload = {
                        'user_id': str(driver_user_id),
                        'driver_id': str(driver_user_id),
                        'title': 'Passenger sent a counter offer',
                        'body': f'Passenger offered PKR {_to_int_pkr(counter_fare, default=0)} per seat.',
                        'data': {
                            'type': 'booking_update',
                            'action': 'passenger_counter',
                            'trip_id': str(trip.trip_id),
                            'booking_id': str(booking.id),
                            'seats': str(getattr(booking, 'number_of_seats', '') or ''),
                            'counter_fare': str(_to_int_pkr(counter_fare, default=0)),
                            'from_stop_name': str(getattr(getattr(booking, 'from_stop', None), 'stop_name', '') or ''),
                            'to_stop_name': str(getattr(getattr(booking, 'to_stop', None), 'stop_name', '') or ''),
                            'from_stop_order': str(getattr(getattr(booking, 'from_stop', None), 'stop_order', '') or ''),
                            'to_stop_order': str(getattr(getattr(booking, 'to_stop', None), 'stop_order', '') or ''),
                            'sender_id': str(booking.passenger_id),
                            'sender_name': str(getattr(booking, 'passenger', None).name if getattr(booking, 'passenger', None) else 'Passenger'),
                            'sender_role': 'passenger',
                            'sender_photo_url': str(getattr(getattr(booking, 'passenger', None), 'profile_photo_url', '') or ''),
                        },
                    }
                    print(f"[passenger_respond_booking] Calling send_ride_notification_async with payload={payload}")
                    send_ride_notification_async(payload)
            except Exception as e:
                print('[passenger_respond_booking][notify_error][counter]:', e)
            print(f"[passenger_respond_booking] END action=counter total_elapsed={(pytime.time()-t0)*1000:.1f}ms")
            return JsonResponse({'success': True, 'message': 'Counter offer submitted', 'booking': {
                'id': booking.id,
                'status': booking.booking_status,
                'bargaining_status': booking.bargaining_status,
                'passenger_offer': int(booking.passenger_offer) if booking.passenger_offer is not None else None,
            }})
        elif action == 'withdraw':
            booking.booking_status = 'CANCELLED'
            booking.bargaining_status = 'WITHDRAWN'
            setattr(booking, 'passenger_response', note)
            booking.save()
            try:
                if getattr(booking, 'seats_locked', False):
                    Trip.objects.filter(id=trip.id).update(
                        available_seats=F('available_seats') + (booking.number_of_seats or 0)
                    )
                    booking.seats_locked = False
                    booking.save(update_fields=['seats_locked', 'updated_at'])
            except Exception:
                pass
            try:
                hist = trip.bargaining_history or []
                hist.append({
                    'action': 'passenger_withdraw',
                    'passenger_id': booking.passenger_id,
                    'booking_id': booking.id,
                    'note': note,
                    'ts': timezone.now().isoformat(),
                })
                trip.bargaining_history = hist
                trip.save(update_fields=['bargaining_history'])
            except Exception as e:
                print('[passenger_respond_booking][history_error][withdraw]:', e)
            # Notify driver about withdrawal
            try:
                driver_user_id = getattr(trip, 'driver_id', None)
                if driver_user_id:
                    payload = {
                        'user_id': str(driver_user_id),
                        'driver_id': str(driver_user_id),
                        'title': 'Passenger withdrew request',
                        'body': 'Passenger withdrew their booking request.',
                        'data': {
                            'type': 'booking_update',
                            'action': 'passenger_withdraw',
                            'trip_id': str(trip.trip_id),
                            'booking_id': str(booking.id),
                            'seats': str(getattr(booking, 'number_of_seats', '') or ''),
                            'from_stop_name': str(getattr(getattr(booking, 'from_stop', None), 'stop_name', '') or ''),
                            'to_stop_name': str(getattr(getattr(booking, 'to_stop', None), 'stop_name', '') or ''),
                            'from_stop_order': str(getattr(getattr(booking, 'from_stop', None), 'stop_order', '') or ''),
                            'to_stop_order': str(getattr(getattr(booking, 'to_stop', None), 'stop_order', '') or ''),
                            'sender_id': str(booking.passenger_id),
                            'sender_name': str(getattr(booking, 'passenger', None).name if getattr(booking, 'passenger', None) else 'Passenger'),
                            'sender_role': 'passenger',
                            'sender_photo_url': str(getattr(getattr(booking, 'passenger', None), 'profile_photo_url', '') or ''),
                        },
                    }
                    print(f"[passenger_respond_booking] Calling send_ride_notification_async with payload={payload}")
                    send_ride_notification_async(payload)
            except Exception as e:
                print('[passenger_respond_booking][notify_error][withdraw]:', e)
            print(f"[passenger_respond_booking] END action=withdraw total_elapsed={(pytime.time()-t0)*1000:.1f}ms")
            return JsonResponse({'success': True, 'message': 'Booking request withdrawn', 'booking': {
                'id': booking.id,
                'status': booking.booking_status,
                'bargaining_status': booking.bargaining_status,
            }})
        else:
            return JsonResponse({'success': False, 'error': 'Unsupported action'}, status=400)
    except Trip.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)
    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)
    except Exception as e:
        import traceback
        print('[PASSENGER_RESPOND][ERROR]', e)
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ================= Negotiation history =================

@csrf_exempt
def get_booking_negotiation_history(request, trip_id, booking_id):
    """Return negotiation history for a specific booking on a trip.

    This mirrors chat history behavior: frontend can first load full history,
    then poll/refresh when new negotiation events arrive.
    """
    if request.method != 'GET':
        return JsonResponse({'success': False, 'error': 'Only GET allowed'}, status=405)
    try:
        trip = Trip.objects.only('id', 'trip_id', 'bargaining_history').get(trip_id=trip_id)
        booking = (
            Booking.objects
            .select_related('from_stop', 'to_stop')
            .only(
                'id', 'trip_id', 'booking_status', 'bargaining_status',
                'original_fare', 'negotiated_fare', 'passenger_offer',
                'number_of_seats', 'from_stop_id', 'to_stop_id', 'total_fare'
            )
            .get(id=booking_id, trip_id=trip.id)
        )

        all_events = trip.bargaining_history or []
        # Filter events by booking_id; be tolerant of missing or string ids
        events = [
            e for e in all_events
            if str(e.get('booking_id')) == str(booking.id)
        ]

        # Normalize legacy action values for UI clarity
        normalized = []
        for e in events:
            if not isinstance(e, dict):
                continue
            e2 = dict(e)
            action = (e2.get('action') or '').lower()
            if action == 'counter':
                e2['action'] = 'driver_counter'
            elif action == 'accept':
                e2['action'] = 'driver_accept'
            normalized.append(e2)

        final_fare_per_seat = None
        try:
            # Prefer explicit accepted_fare from latest accept event
            for ev in reversed(normalized):
                a = (ev.get('action') or '').lower()
                if a in ['driver_accept', 'passenger_accept']:
                    af = ev.get('accepted_fare_per_seat')
                    if af is None:
                        af = ev.get('accepted_fare')
                    if af is not None:
                        final_fare_per_seat = _to_int_pkr(af, default=None)
                        break

            if final_fare_per_seat is None:
                # Otherwise infer from latest offer event
                for ev in reversed(normalized):
                    a = (ev.get('action') or '').lower()
                    if a == 'passenger_counter' and booking.passenger_offer is not None:
                        final_fare_per_seat = int(booking.passenger_offer)
                        break
                    if a in ['driver_counter', 'counter'] and booking.negotiated_fare is not None:
                        final_fare_per_seat = int(booking.negotiated_fare)
                        break

            if final_fare_per_seat is None:
                # If accepted/confirmed, negotiated_fare is the final per-seat value.
                if booking.bargaining_status == 'ACCEPTED' and booking.negotiated_fare is not None:
                    final_fare_per_seat = int(booking.negotiated_fare)

            if final_fare_per_seat is None and booking.total_fare is not None:
                # total_fare is TOTAL for all seats; compute per-seat for display.
                seats = int(booking.number_of_seats or 0)
                if seats > 0:
                    final_fare_per_seat = _to_int_pkr(float(booking.total_fare) / float(seats), default=None)
        except Exception:
            final_fare_per_seat = None

        final_booking_statuses = ['CONFIRMED', 'CANCELLED', 'COMPLETED']
        final_bargaining_statuses = ['ACCEPTED', 'REJECTED', 'WITHDRAWN', 'BLOCKED', 'BLACKLISTED']

        is_final = (
            booking.booking_status in final_booking_statuses or
            booking.bargaining_status in final_bargaining_statuses
        )

        return JsonResponse({
            'success': True,
            'booking': {
                'id': booking.id,
                'booking_status': booking.booking_status,
                'bargaining_status': booking.bargaining_status,
                'from_stop_name': booking.from_stop.stop_name if getattr(booking, 'from_stop', None) else None,
                'to_stop_name': booking.to_stop.stop_name if getattr(booking, 'to_stop', None) else None,
                'number_of_seats': booking.number_of_seats,
                'male_seats': int(getattr(booking, 'male_seats', 0) or 0),
                'female_seats': int(getattr(booking, 'female_seats', 0) or 0),
                'original_fare': int(booking.original_fare) if booking.original_fare is not None else None,
                'negotiated_fare': int(booking.negotiated_fare) if booking.negotiated_fare is not None else None,
                'passenger_offer': int(booking.passenger_offer) if booking.passenger_offer is not None else None,
                'total_fare': int(booking.total_fare) if booking.total_fare is not None else None,
                'final_fare_per_seat': final_fare_per_seat,
            },
            'history': normalized,
            'can_respond': not is_final,
        })
    except Trip.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)
    except Booking.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ================= Legacy non-bargaining passenger request =================

@csrf_exempt
def request_ride_booking(request, trip_id):
    """Legacy: Request a ride booking without detailed bargaining (kept for compatibility)."""
    if request.method == 'POST':
        try:
            t0 = timezone.now()
            data = json.loads(request.body.decode('utf-8'))

            # Extract booking data
            passenger_id = data.get('passenger_id')
            from_stop_order = data.get('from_stop_order')
            to_stop_order = data.get('to_stop_order')
            number_of_seats = int(data.get('number_of_seats', 1) or 1)
            special_requests = data.get('special_requests', '')

            if not all([passenger_id, from_stop_order, to_stop_order, number_of_seats]):
                return JsonResponse({
                    'success': False,
                    'error': 'Missing required fields: passenger_id, from_stop_order, to_stop_order, number_of_seats'
                }, status=400)

            # Short transaction with row lock to avoid race conditions and long locks
            with transaction.atomic():
                t1 = timezone.now()
                trip = (
                    Trip.objects
                    .select_for_update(skip_locked=True)
                    .only('id', 'trip_id', 'trip_status', 'available_seats', 'base_fare', 'route_id', 'driver_id')
                    .select_related('route')
                    .get(trip_id=trip_id)
                )
                t2 = timezone.now()
                print(f"[request_ride_booking] Trip lock fetch {(t2 - t1).total_seconds()*1000:.1f}ms")

                if trip.trip_status != 'SCHEDULED':
                    return JsonResponse({'success': False, 'error': 'Trip is not available for booking'}, status=400)
                if trip.available_seats < number_of_seats:
                    return JsonResponse({'success': False, 'error': f'Only {trip.available_seats} seats available'}, status=400)

                try:
                    passenger = UsersData.objects.only('id').get(id=passenger_id)
                except UsersData.DoesNotExist:
                    return JsonResponse({'success': False, 'error': 'Passenger not found'}, status=404)

                blocked = ride_booking_block_response(passenger.id)
                if blocked is not None:
                    return blocked

                # Block checks (per-trip and persistent)
                try:
                    if Booking.objects.filter(trip_id=trip.id, passenger_id=passenger.id, blocked=True).only('id').exists():
                        return JsonResponse({'success': False, 'error': 'You are blocked from requesting this ride.'}, status=403)
                except Exception:
                    pass
                try:
                    if BlockedUser.objects.filter(blocker_id=trip.driver_id, blocked_user_id=passenger.id).only('id').exists():
                        return JsonResponse({'success': False, 'error': 'You are blocked by this driver.'}, status=403)
                except Exception:
                    pass

                # Fast existence check
                if Booking.objects.filter(trip_id=trip.id, passenger_id=passenger.id, booking_status='CONFIRMED').only('id').exists():
                    return JsonResponse({'success': False, 'error': 'You already have a booking for this trip'}, status=400)

                # Fetch route stops once
                stops_qs = RouteStop.objects.filter(route=trip.route).only('id', 'stop_order')
                stop_by_order = {int(s.stop_order): s for s in stops_qs}
                if from_stop_order not in stop_by_order or to_stop_order not in stop_by_order:
                    return JsonResponse({'success': False, 'error': 'Invalid stop selection'}, status=400)

                from_stop = stop_by_order[from_stop_order]
                to_stop = stop_by_order[to_stop_order]

                booking = Booking.objects.create(
                    trip_id=trip.id,
                    passenger_id=passenger.id,
                    from_stop_id=from_stop.id,
                    to_stop_id=to_stop.id,
                    number_of_seats=number_of_seats,
                    total_fare=int(trip.base_fare or 0) * number_of_seats,
                    booking_status='CONFIRMED',
                    payment_status='PENDING'
                )

            # Fire-and-forget notification to the driver via Supabase Edge Function
            try:
                driver_user_id = getattr(trip, 'driver_id', None)
                print(f"[request_ride_booking] driver_user_id={driver_user_id}")
                if driver_user_id:
                    payload = {
                        'user_id': str(driver_user_id),
                        'driver_id': str(driver_user_id),
                        'title': 'New ride request',
                        'body': f'Passenger requested {number_of_seats} seat(s).',
                        'data': {
                            'type': 'ride_request',
                            'trip_id': str(trip.trip_id),
                            'booking_id': str(booking.id),
                            'seats': str(number_of_seats),
                        },
                    }
                    print(f"[request_ride_booking] Calling send_ride_notification_async with payload={payload}")
                    send_ride_notification_async(payload)
            except Exception as e:
                print('[request_ride_booking][notify_error]:', e)

            t3 = timezone.now()
            print(f"[request_ride_booking] Total elapsed {(t3 - t0).total_seconds()*1000:.1f}ms")

            return JsonResponse({
                'success': True,
                'message': 'Ride booking requested successfully',
                'booking_id': booking.id,
                'status': booking.booking_status,
                'total_fare': int(booking.total_fare) if booking.total_fare is not None else 0
            }, status=201)

        except Trip.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Trip not found'
            }, status=404)
        except UsersData.DoesNotExist:
            return JsonResponse({
                'success': False,
                'error': 'Passenger not found'
            }, status=404)
        except (OperationalError, DatabaseError) as e:
            print('[request_ride_booking][DB_ERROR]:', e)
            return JsonResponse({'success': False, 'error': 'Database busy or connection issue. Please retry.'}, status=503)
        except Exception as e:
            return JsonResponse({
                'success': False,
                'error': f'Error creating booking: {str(e)}'
            }, status=500)

    return JsonResponse({
        'success': False,
        'error': 'Method not allowed'
    }, status=405)
