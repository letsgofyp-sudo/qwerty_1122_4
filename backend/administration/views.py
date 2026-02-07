# Add user creation view (GET: show form, POST: save user)
from django.http import HttpResponseRedirect, JsonResponse
from django.contrib.auth import authenticate, login, logout
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from datetime import timedelta
from django.views.decorators.http import require_http_methods
from django.utils import timezone
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q, Sum, Max
from django.db.models.functions import Coalesce, ExtractHour
from lets_go.models import (
    GuestUser,
    UsersData,
    EmergencyContact,
    Vehicle,
    Trip,
    Booking,
    TripStopBreakdown,
    TripPayment,
    TripChatGroup,
    ChatMessage,
    SosIncident,
    ChangeRequest,
    SupportThread,
    SupportMessage,
)
import base64
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.hashers import make_password
from django.utils import timezone
from django.contrib.auth.decorators import login_required

from lets_go.views_notifications import send_ride_notification_async


def _attach_latest_payments(bookings):
    booking_ids = [b.id for b in bookings]
    if not booking_ids:
        return

    payment_map = {}
    try:
        payments = (
            TripPayment.objects
            .filter(booking_id__in=booking_ids)
            .only('booking_id', 'payment_method', 'payment_status', 'receipt_url', 'created_at', 'completed_at')
            .order_by('-created_at')
        )
        for p in payments:
            if p.booking_id not in payment_map:
                payment_map[p.booking_id] = p
    except Exception:
        payment_map = {}

    for b in bookings:
        p = payment_map.get(b.id)
        setattr(b, 'latest_payment', p)
        setattr(b, 'latest_receipt_url', getattr(p, 'receipt_url', None) if p is not None else None)
        setattr(b, 'latest_payment_method', getattr(p, 'payment_method', None) if p is not None else None)


def guest_list_view(request):
    return render(request, 'administration/guests_list.html')


def api_guests(request):
    qs = GuestUser.objects.all().values(
        'id',
        'guest_number',
        'username',
        'created_at',
        'updated_at',
    )
    return JsonResponse({'guests': list(qs)})


@login_required
def guest_support_chat_view(request, guest_id):
    guest = get_object_or_404(GuestUser, pk=guest_id)
    thread, _ = SupportThread.objects.get_or_create(
        user=None,
        guest=guest,
        thread_type='ADMIN',
        defaults={'last_message_at': timezone.now()},
    )

    latest_id = SupportMessage.objects.filter(thread=thread).aggregate(mx=Max('id')).get('mx') or 0
    if latest_id and thread.admin_last_seen_id != latest_id:
        thread.admin_last_seen_id = latest_id
        thread.save(update_fields=['admin_last_seen_id', 'updated_at'])

    error = None
    if request.method == 'POST':
        message_text = (request.POST.get('message_text') or '').strip()
        if not message_text:
            error = 'Message cannot be empty.'
        else:
            admin_sender = None
            try:
                admin_sender = UsersData.objects.filter(username=getattr(request.user, 'username', '')).first()
            except Exception:
                admin_sender = None

            SupportMessage.objects.create(
                thread=thread,
                sender_type='ADMIN',
                sender_user=admin_sender,
                message_text=message_text,
            )

            latest_id = SupportMessage.objects.filter(thread=thread).aggregate(mx=Max('id')).get('mx') or 0
            if latest_id:
                thread.admin_last_seen_id = latest_id
                thread.save(update_fields=['admin_last_seen_id', 'updated_at'])

            thread.last_message_at = timezone.now()
            thread.save(update_fields=['last_message_at', 'updated_at'])

            try:
                payload = {
                    'recipient_id': str(guest.username),
                    'sender_id': str(admin_sender.id) if admin_sender is not None else '0',
                    'user_id': str(guest.username),
                    'driver_id': str(admin_sender.id) if admin_sender is not None else '0',
                    'title': 'Admin Support Reply',
                    'body': message_text[:140],
                    'data': {
                        'type': 'support_admin',
                        'thread_type': 'ADMIN',
                        'guest_user_id': str(guest.id),
                        'guest_username': str(guest.username),
                        'sender_id': str(admin_sender.id) if admin_sender is not None else '0',
                        'sender_name': str(getattr(admin_sender, 'name', '') or 'Admin'),
                        'sender_photo_url': str(getattr(admin_sender, 'profile_photo_url', '') or ''),
                        'message_text': message_text,
                    },
                }
                send_ride_notification_async(payload)
            except Exception:
                pass

            return redirect('administration:guest_support_chat', guest_id=guest.id)

    messages = SupportMessage.objects.filter(thread=thread).order_by('created_at')
    return render(
        request,
        'administration/guest_support_chat.html',
        {
            'guest': guest,
            'thread': thread,
            'messages': messages,
            'error': error,
        },
    )

@csrf_protect
def user_add_view(request):
    if request.method == 'POST':
        user = UsersData()
        user.name = request.POST.get('name')
        user.username = request.POST.get('username')
        user.email = request.POST.get('email')
        raw_password = request.POST.get('password')
        user.password = make_password(raw_password) if raw_password else None
        user.address = request.POST.get('address')
        phone_no = request.POST.get('phone_no')
        # Ensure phone number has + prefix for international format
        if phone_no and not phone_no.startswith('+'):
            phone_no = '+' + phone_no
        user.phone_no = phone_no
        user.gender = request.POST.get('gender')
        user.status = request.POST.get('status') or 'PENDING'
        user.driver_rating = request.POST.get('driver_rating') or None
        user.passenger_rating = request.POST.get('passenger_rating') or None
        user.cnic_no = request.POST.get('cnic_no')
        user.driving_license_no = request.POST.get('driving_license_no')
        user.accountno = request.POST.get('accountno')
        user.iban = request.POST.get('iban')
        user.bankname = request.POST.get('bankname')

        if request.FILES.get('accountqr'):
            user.accountqr = request.FILES['accountqr'].read()
        if request.FILES.get('profile_photo'):
            user.profile_photo = request.FILES['profile_photo'].read()
        if request.FILES.get('live_photo'):
            user.live_photo = request.FILES['live_photo'].read()
        if request.FILES.get('cnic_front_image'):
            user.cnic_front_image = request.FILES['cnic_front_image'].read()
        if request.FILES.get('cnic_back_image'):
            user.cnic_back_image = request.FILES['cnic_back_image'].read()
        if request.FILES.get('driving_license_front'):
            user.driving_license_front = request.FILES['driving_license_front'].read()
        if request.FILES.get('driving_license_back'):
            user.driving_license_back = request.FILES['driving_license_back'].read()
        try:
            user.full_clean()
            user.save()

            emergency_name = (request.POST.get('emergency_name') or '').strip()
            emergency_relation = (request.POST.get('emergency_relation') or '').strip()
            emergency_email = (request.POST.get('emergency_email') or '').strip()
            emergency_phone_no = (request.POST.get('emergency_phone_no') or '').strip()
            if any([emergency_name, emergency_relation, emergency_email, emergency_phone_no]):
                ec = EmergencyContact(
                    user=user,
                    name=emergency_name,
                    relation=emergency_relation,
                    email=emergency_email,
                    phone_no=emergency_phone_no,
                )
                ec.full_clean()
                ec.save()

            return redirect('administration:user_list')
        except Exception as e:
            try:
                if getattr(user, 'id', None):
                    user.delete()
            except Exception:
                pass
            return render(request, 'administration/user_add.html', {'error': str(e)})
    return render(request, 'administration/user_add.html')
# Create your views here.
def admin_view(request):
    return render(request, "administration/index.html")


def analytics_view(request):
    return render(request, 'administration/analytics.html')


def settings_view(request):
    return render(request, 'administration/settings.html')


@login_required
def user_support_chat_view(request, user_id):
    user = get_object_or_404(UsersData, pk=user_id)
    thread, _ = SupportThread.objects.get_or_create(
        user=user,
        thread_type='ADMIN',
        defaults={'last_message_at': timezone.now()},
    )

    bot_thread, _ = SupportThread.objects.get_or_create(
        user=user,
        thread_type='BOT',
        defaults={'last_message_at': timezone.now()},
    )

    latest_id = SupportMessage.objects.filter(thread=thread).aggregate(mx=Max('id')).get('mx') or 0
    if latest_id and thread.admin_last_seen_id != latest_id:
        thread.admin_last_seen_id = latest_id
        thread.save(update_fields=['admin_last_seen_id', 'updated_at'])

    error = None
    if request.method == 'POST':
        message_text = (request.POST.get('message_text') or '').strip()
        if not message_text:
            error = 'Message cannot be empty.'
        else:
            # Admin identity: use the Django auth user if possible, otherwise send without sender_user
            admin_sender = None
            try:
                # Sometimes the admin username is an existing UsersData username
                admin_sender = UsersData.objects.filter(username=getattr(request.user, 'username', '')).first()
            except Exception:
                admin_sender = None

            SupportMessage.objects.create(
                thread=thread,
                sender_type='ADMIN',
                sender_user=admin_sender,
                message_text=message_text,
            )

            latest_id = SupportMessage.objects.filter(thread=thread).aggregate(mx=Max('id')).get('mx') or 0
            if latest_id:
                thread.admin_last_seen_id = latest_id
                thread.save(update_fields=['admin_last_seen_id', 'updated_at'])

            thread.last_message_at = timezone.now()
            thread.save(update_fields=['last_message_at', 'updated_at'])

            # Push notification to user
            try:
                payload = {
                    'recipient_id': str(user.id),
                    'sender_id': str(admin_sender.id) if admin_sender is not None else '0',
                    'user_id': str(user.id),
                    'driver_id': str(admin_sender.id) if admin_sender is not None else '0',
                    'title': 'Admin Support Reply',
                    'body': message_text[:140],
                    'data': {
                        'type': 'support_admin',
                        'thread_type': 'ADMIN',
                        'user_id': str(user.id),
                        'sender_id': str(admin_sender.id) if admin_sender is not None else '0',
                        'sender_name': str(getattr(admin_sender, 'name', '') or 'Admin'),
                        'sender_photo_url': str(getattr(admin_sender, 'profile_photo_url', '') or ''),
                        'message_text': message_text,
                    },
                }
                send_ride_notification_async(payload)
            except Exception:
                pass

            return redirect('administration:user_support_chat', user_id=user.id)

    messages = SupportMessage.objects.filter(thread=thread).order_by('created_at')
    bot_messages = SupportMessage.objects.filter(thread=bot_thread).order_by('created_at')
    return render(
        request,
        'administration/user_support_chat.html',
        {
            'user': user,
            'thread': thread,
            'messages': messages,
            'bot_thread': bot_thread,
            'bot_messages': bot_messages,
            'error': error,
        },
    )


def rides_dashboard_view(request):
    """Admin dashboard to track rides/trips & bookings."""
    today = timezone.now().date()

    total_trips = Trip.objects.count()
    trips_today = Trip.objects.filter(trip_date=today).count()
    in_progress_trips = Trip.objects.filter(trip_status='IN_PROGRESS').count()
    completed_today = Trip.objects.filter(trip_status='COMPLETED', trip_date=today).count()
    total_bookings = Booking.objects.count()

    recent_trips = (
        Trip.objects
        .select_related('route', 'driver', 'vehicle')
        .annotate(
            bookings_count=Count('trip_bookings', distinct=True),
            male_seats_booked=Coalesce(Sum('trip_bookings__male_seats'), 0),
            female_seats_booked=Coalesce(Sum('trip_bookings__female_seats'), 0),
            confirmed_bookings=Count('trip_bookings', filter=Q(trip_bookings__booking_status='CONFIRMED'), distinct=True),
            cancelled_bookings=Count('trip_bookings', filter=Q(trip_bookings__booking_status='CANCELLED'), distinct=True),
            completed_bookings=Count('trip_bookings', filter=Q(trip_bookings__booking_status='COMPLETED'), distinct=True),
            negotiated_bookings=Count('trip_bookings', filter=~Q(trip_bookings__bargaining_status='NO_NEGOTIATION'), distinct=True),
            paid_bookings=Count('trip_bookings', filter=Q(trip_bookings__payment_status='COMPLETED'), distinct=True),
        )
        .annotate(
            seats_booked=F('male_seats_booked') + F('female_seats_booked'),
        )
        .order_by('-trip_date', '-departure_time')[:30]
    )

    context = {
        'total_trips': total_trips,
        'trips_today': trips_today,
        'in_progress_trips': in_progress_trips,
        'completed_today': completed_today,
        'total_bookings': total_bookings,
        'recent_trips': recent_trips,
        'today': today,
    }
    return render(request, 'administration/rides_dashboard.html', context)


def admin_trip_detail_view(request, trip_pk):
    """Admin detail page for a single trip with full related info."""
    trip = get_object_or_404(Trip.objects.select_related('route', 'driver', 'vehicle'), pk=trip_pk)

    trip_meta_data = {
        'trip_id': trip.trip_id,
        'driver_id': trip.driver_id,
        'driver_name': getattr(trip.driver, 'name', None),
        'driver_profile_photo': getattr(trip.driver, 'profile_photo_url', None),
    }

    # All bookings for this trip with passengers and stops
    bookings = (
        Booking.objects
        .filter(trip=trip)
        .select_related('passenger', 'from_stop', 'to_stop')
        .prefetch_related('payments')
        .order_by('-booked_at')
    )

    _attach_latest_payments(bookings)

    # Booking tabs for UI, grouped by (passenger, seats, from, to) so identical lines are not repeated
    tab_groups = {}
    for b in bookings:
        from_name = b.from_stop.stop_name if b.from_stop else ''
        to_name = b.to_stop.stop_name if b.to_stop else ''

        male_seats = int(getattr(b, 'male_seats', 0) or 0)
        female_seats = int(getattr(b, 'female_seats', 0) or 0)
        total_seats = int(getattr(b, 'number_of_seats', 0) or 0)
        if (male_seats + female_seats) > 0:
            total_seats = male_seats + female_seats
        if total_seats <= 0:
            total_seats = 1

        if (male_seats + female_seats) > 0:
            seats_display = f"{total_seats} (M:{male_seats} F:{female_seats})"
        else:
            seats_display = str(total_seats)

        key = (b.passenger.id, male_seats, female_seats, from_name, to_name)
        if key not in tab_groups:
            tab_groups[key] = {
                'id': b.id,  # representative booking id for this group
                'passenger_id': b.passenger.id,
                'passenger_name': b.passenger.name,
                'seats': total_seats,
                'male_seats': male_seats,
                'female_seats': female_seats,
                'seats_display': seats_display,
                'from_name': from_name,
                'to_name': to_name,
                'booking_ids': [b.id],
            }
        else:
            tab_groups[key]['booking_ids'].append(b.id)

    booking_tabs = list(tab_groups.values())

    # Stop breakdown segments, if present
    segments = TripStopBreakdown.objects.filter(trip=trip).order_by('from_stop_order')
    segments_coords_data = []
    for s in segments:
        try:
            if s.from_latitude and s.from_longitude and s.to_latitude and s.to_longitude:
                segments_coords_data.append([
                    [float(s.from_latitude), float(s.from_longitude)],
                    [float(s.to_latitude), float(s.to_longitude)],
                ])
        except Exception:
            pass

    if getattr(trip, 'total_duration_minutes', None) is None:
        try:
            total_duration = segments.aggregate(total=Sum('duration_minutes'))['total']
        except Exception:
            total_duration = None
        try:
            trip.total_duration_minutes = int(total_duration) if total_duration is not None else None
        except Exception:
            trip.total_duration_minutes = None

    # Full ordered route stops for this trip (used for main map polyline & markers)
    route_stops_full = []
    route_geometry = []
    route_stops_full_data = []
    route_geometry_data = []
    try:
        route = getattr(trip, 'route', None)
        if route is not None:
            route_stops_full = list(route.route_stops.all().order_by('stop_order'))
            route_geometry = route.route_geometry or []
            for rs in route_stops_full:
                if getattr(rs, 'latitude', None) and getattr(rs, 'longitude', None):
                    route_stops_full_data.append({
                        'name': rs.stop_name,
                        'order': rs.stop_order,
                        'lat': float(rs.latitude),
                        'lng': float(rs.longitude),
                    })
            for p in route_geometry:
                try:
                    if isinstance(p, dict) and 'lat' in p and 'lng' in p:
                        route_geometry_data.append({'lat': float(p['lat']), 'lng': float(p['lng'])})
                except Exception:
                    pass
    except Exception:
        route_stops_full = []
        route_geometry = []
        route_stops_full_data = []
        route_geometry_data = []

    booking_markers_data = []
    booking_meta_data = []
    for b in bookings:
        try:
            from_lat = None
            from_lng = None
            to_lat = None
            to_lng = None
            try:
                if b.from_stop and b.from_stop.latitude is not None and b.from_stop.longitude is not None:
                    from_lat = float(b.from_stop.latitude)
                    from_lng = float(b.from_stop.longitude)
            except Exception:
                pass
            try:
                if b.to_stop and b.to_stop.latitude is not None and b.to_stop.longitude is not None:
                    to_lat = float(b.to_stop.latitude)
                    to_lng = float(b.to_stop.longitude)
            except Exception:
                pass

            booking_meta_data.append({
                'booking_id': b.id,
                'booking_code': getattr(b, 'booking_id', None),
                'passenger_id': b.passenger_id,
                'passenger_name': getattr(getattr(b, 'passenger', None), 'name', None),
                'passenger_profile_photo': getattr(getattr(b, 'passenger', None), 'profile_photo_url', None),
                'from_stop_name': getattr(getattr(b, 'from_stop', None), 'stop_name', None),
                'to_stop_name': getattr(getattr(b, 'to_stop', None), 'stop_name', None),
                'from_stop_lat': from_lat,
                'from_stop_lng': from_lng,
                'to_stop_lat': to_lat,
                'to_stop_lng': to_lng,
                'passenger_to_driver_rating': float(b.driver_rating) if getattr(b, 'driver_rating', None) is not None else None,
                'passenger_to_driver_comment': getattr(b, 'driver_feedback', None),
                'driver_to_passenger_rating': float(b.passenger_rating) if getattr(b, 'passenger_rating', None) is not None else None,
                'driver_to_passenger_comment': getattr(b, 'passenger_feedback', None),
            })
        except Exception:
            pass
        try:
            if b.from_stop and b.from_stop.latitude and b.from_stop.longitude:
                booking_markers_data.append({
                    'type': 'pickup',
                    'lat': float(b.from_stop.latitude),
                    'lng': float(b.from_stop.longitude),
                    'label': f"Pickup: {b.passenger.name} ({b.from_stop.stop_name})",
                })
        except Exception:
            pass
        try:
            if b.to_stop and b.to_stop.latitude and b.to_stop.longitude:
                booking_markers_data.append({
                    'type': 'dropoff',
                    'lat': float(b.to_stop.latitude),
                    'lng': float(b.to_stop.longitude),
                    'label': f"Drop-off: {b.passenger.name} ({b.to_stop.stop_name})",
                })
        except Exception:
            pass

    # Aggregate payment info for this trip
    payments = TripPayment.objects.filter(booking__trip=trip).select_related('booking')

    payments_total = payments.aggregate(total_amount=Sum('amount'))['total_amount'] or 0
    payments_completed = payments.filter(payment_status='COMPLETED').aggregate(total_amount=Sum('amount'))['total_amount'] or 0

    # Chat: full messages and members
    chat_group = getattr(trip, 'chat_group', None)
    messages = []
    members = []
    if chat_group:
        messages = ChatMessage.objects.filter(chat_group=chat_group, is_deleted=False).select_related('sender').order_by('created_at')
        # Use related manager to fetch members with user details
        members = chat_group.chat_members.select_related('user').all()

    sos_incidents = (
        SosIncident.objects
        .filter(trip=trip, status='OPEN')
        .select_related('actor', 'booking')
        .order_by('-created_at')
    )
    sos_markers_data = []
    for i in sos_incidents:
        try:
            sos_markers_data.append({
                'id': i.id,
                'lat': float(i.latitude),
                'lng': float(i.longitude),
                'role': i.role,
                'booking_id': i.booking_id,
                'actor_id': i.actor_id,
                'actor_name': getattr(getattr(i, 'actor', None), 'name', None),
                'note': i.note,
                'created_at': i.created_at.isoformat() if getattr(i, 'created_at', None) else None,
            })
        except Exception:
            pass

    context = {
        'trip': trip,
        'trip_meta_data': trip_meta_data,
        'bookings': bookings,
        'segments': segments,
        'segments_coords_data': segments_coords_data,
        'route_stops_full': route_stops_full,
        'route_stops_full_data': route_stops_full_data,
        'route_geometry': route_geometry,
        'route_geometry_data': route_geometry_data,
        'booking_markers_data': booking_markers_data,
        'booking_meta_data': booking_meta_data,
        'payments_total': payments_total,
        'payments_completed': payments_completed,
        'chat_group': chat_group,
        'messages': messages,
        'members': members,
        'booking_tabs': booking_tabs,
        'sos_incidents': sos_incidents,
        'sos_markers_data': sos_markers_data,
    }
    return render(request, 'administration/trip_detail.html', context)


@require_http_methods(['GET'])
def change_requests_list_view(request):
    qs = (
        ChangeRequest.objects
        .select_related('user', 'vehicle')
        .only(
            'id', 'entity_type', 'status', 'created_at',
            'user__id', 'user__name',
            'vehicle__id', 'vehicle__plate_number',
        )
        .order_by('-created_at')
    )

    status_filter = (request.GET.get('status') or '').strip().upper()
    if status_filter in [ChangeRequest.STATUS_PENDING, ChangeRequest.STATUS_APPROVED, ChangeRequest.STATUS_REJECTED]:
        qs = qs.filter(status=status_filter)
    else:
        status_filter = ''

    entity_filter = (request.GET.get('entity_type') or '').strip().upper()
    if entity_filter in [ChangeRequest.ENTITY_USER_PROFILE, ChangeRequest.ENTITY_VEHICLE]:
        qs = qs.filter(entity_type=entity_filter)
    else:
        entity_filter = ''

    return render(request, 'administration/change_requests_list.html', {
        'change_requests': qs[:300],
        'status_filter': status_filter,
        'entity_filter': entity_filter,
    })


@require_http_methods(['GET', 'POST'])
@csrf_protect
def change_request_detail_view(request, change_request_id):
    cr = get_object_or_404(ChangeRequest.objects.select_related('user', 'vehicle'), pk=change_request_id)

    compare_rows = []
    try:
        keys = set()
        keys.update((cr.original_data or {}).keys())
        keys.update((cr.requested_changes or {}).keys())
        for k in sorted(keys):
            compare_rows.append({
                'field': k,
                'old': (cr.original_data or {}).get(k),
                'new': (cr.requested_changes or {}).get(k),
            })
    except Exception:
        compare_rows = []

    error = None
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip().lower()
        notes = (request.POST.get('review_notes') or '').strip() or None

        if cr.status != ChangeRequest.STATUS_PENDING:
            error = 'This request is already reviewed.'
        elif action not in ['approve', 'reject']:
            error = 'Invalid action.'
        else:
            try:
                if action == 'approve':
                    if cr.entity_type == ChangeRequest.ENTITY_USER_PROFILE:
                        user = cr.user
                        for k, v in (cr.requested_changes or {}).items():
                            setattr(user, k, v)
                        user.full_clean()
                        user.save()
                    elif cr.entity_type == ChangeRequest.ENTITY_VEHICLE:
                        vehicle = cr.vehicle
                        if vehicle is None:
                            raise ValueError('Vehicle not found for this change request.')
                        for k, v in (cr.requested_changes or {}).items():
                            setattr(vehicle, k, v)
                        vehicle.full_clean()
                        vehicle.status = Vehicle.STATUS_VERIFIED
                        vehicle.save()
                    else:
                        raise ValueError('Unknown entity type.')

                    cr.status = ChangeRequest.STATUS_APPROVED
                    cr.review_notes = notes
                    cr.reviewed_at = timezone.now()
                    cr.save(update_fields=['status', 'review_notes', 'reviewed_at'])

                elif action == 'reject':
                    if cr.entity_type == ChangeRequest.ENTITY_VEHICLE and cr.vehicle is not None:
                        vehicle = cr.vehicle
                        if getattr(vehicle, 'status', None) == Vehicle.STATUS_PENDING:
                            vehicle.status = Vehicle.STATUS_REJECTED
                            vehicle.save(update_fields=['status'])

                    cr.status = ChangeRequest.STATUS_REJECTED
                    cr.review_notes = notes
                    cr.reviewed_at = timezone.now()
                    cr.save(update_fields=['status', 'review_notes', 'reviewed_at'])

                return redirect('administration:change_request_detail', change_request_id=cr.id)
            except Exception as e:
                error = str(e)

    return render(request, 'administration/change_request_detail.html', {
        'cr': cr,
        'compare_rows': compare_rows,
        'error': error,
    })


def admin_booking_map_view(request, booking_pk):
    """Admin page to visualize a single booking on the map with distance and price totals."""
    booking = get_object_or_404(
        Booking.objects.select_related('trip', 'from_stop', 'to_stop', 'passenger'),
        pk=booking_pk,
    )
    trip = booking.trip

    trip_meta_data = {
        'trip_id': trip.trip_id,
        'driver_id': trip.driver_id,
        'driver_name': getattr(getattr(trip, 'driver', None), 'name', None),
        'driver_profile_photo': getattr(getattr(trip, 'driver', None), 'profile_photo_url', None),
    }

    _attach_latest_payments([booking])

    from_order = booking.from_stop.stop_order
    to_order = booking.to_stop.stop_order

    booking_span_data = {
        'from_order': from_order,
        'to_order': to_order,
    }

    booking_meta_data = {
        'booking_id': booking.id,
        'booking_code': getattr(booking, 'booking_id', None),
        'passenger_id': booking.passenger_id,
        'passenger_name': getattr(getattr(booking, 'passenger', None), 'name', None),
        'passenger_profile_photo': getattr(getattr(booking, 'passenger', None), 'profile_photo_url', None),
        'from_stop_name': getattr(getattr(booking, 'from_stop', None), 'stop_name', None),
        'to_stop_name': getattr(getattr(booking, 'to_stop', None), 'stop_name', None),
        'from_stop_lat': float(booking.from_stop.latitude) if (getattr(booking, 'from_stop', None) is not None and getattr(booking.from_stop, 'latitude', None) is not None) else None,
        'from_stop_lng': float(booking.from_stop.longitude) if (getattr(booking, 'from_stop', None) is not None and getattr(booking.from_stop, 'longitude', None) is not None) else None,
        'to_stop_lat': float(booking.to_stop.latitude) if (getattr(booking, 'to_stop', None) is not None and getattr(booking.to_stop, 'latitude', None) is not None) else None,
        'to_stop_lng': float(booking.to_stop.longitude) if (getattr(booking, 'to_stop', None) is not None and getattr(booking.to_stop, 'longitude', None) is not None) else None,
        'passenger_to_driver_rating': float(booking.driver_rating) if getattr(booking, 'driver_rating', None) is not None else None,
        'passenger_to_driver_comment': getattr(booking, 'driver_feedback', None),
        'driver_to_passenger_rating': float(booking.passenger_rating) if getattr(booking, 'passenger_rating', None) is not None else None,
        'driver_to_passenger_comment': getattr(booking, 'passenger_feedback', None),
    }

    segments = (
        TripStopBreakdown.objects
        .filter(trip=trip, from_stop_order__gte=from_order, to_stop_order__lte=to_order)
        .order_by('from_stop_order')
    )

    segments_path_coords_data = []
    for s in segments:
        try:
            if s.from_latitude and s.from_longitude and s.to_latitude and s.to_longitude:
                segments_path_coords_data.append([float(s.from_latitude), float(s.from_longitude)])
                segments_path_coords_data.append([float(s.to_latitude), float(s.to_longitude)])
        except Exception:
            pass

    agg = segments.aggregate(
        total_distance=Sum('distance_km'),
        total_price=Sum('price'),
    )

    # Route stops for this trip (full route, so we can show grey markers outside booking span)
    route_stops = []
    route_stops_data = []
    try:
        route = getattr(trip, 'route', None)
        if route is not None:
            route_stops = list(
                route.route_stops
                .all()
                .order_by('stop_order')
            )
            for rs in route_stops:
                if getattr(rs, 'latitude', None) and getattr(rs, 'longitude', None):
                    route_stops_data.append({
                        'name': rs.stop_name,
                        'order': rs.stop_order,
                        'lat': float(rs.latitude),
                        'lng': float(rs.longitude),
                    })
    except Exception:
        # If for some reason route stops cannot be loaded, fall back gracefully
        route_stops = []
        route_stops_data = []

    # Dense geometry for the whole route, if available
    route_geometry = []
    route_geometry_data = []
    try:
        route = getattr(trip, 'route', None)
        if route is not None:
            route_geometry = route.route_geometry or []
            for p in route_geometry:
                try:
                    if isinstance(p, dict) and 'lat' in p and 'lng' in p:
                        route_geometry_data.append({'lat': float(p['lat']), 'lng': float(p['lng'])})
                except Exception:
                    pass
    except Exception:
        route_geometry = []
        route_geometry_data = []

    context = {
        'booking': booking,
        'trip': trip,
        'trip_meta_data': trip_meta_data,
        'booking_meta_data': booking_meta_data,
        'segments': segments,
        'segments_path_coords_data': segments_path_coords_data,
        'total_distance': agg['total_distance'] or 0,
        'total_price': agg['total_price'] or 0,
        'route_stops': route_stops,
        'route_stops_data': route_stops_data,
        'route_geometry': route_geometry,
        'route_geometry_data': route_geometry_data,
        'booking_from_order': from_order,
        'booking_to_order': to_order,
        'booking_span_data': booking_span_data,
    }

    sos_incidents = (
        SosIncident.objects
        .filter(trip=trip, status='OPEN')
        .select_related('actor', 'booking')
        .order_by('-created_at')
    )
    sos_markers_data = []
    for i in sos_incidents:
        try:
            sos_markers_data.append({
                'id': i.id,
                'lat': float(i.latitude),
                'lng': float(i.longitude),
                'role': i.role,
                'booking_id': i.booking_id,
                'actor_id': i.actor_id,
                'actor_name': getattr(getattr(i, 'actor', None), 'name', None),
                'note': i.note,
                'created_at': i.created_at.isoformat() if getattr(i, 'created_at', None) else None,
            })
        except Exception:
            pass

    context['sos_incidents'] = sos_incidents
    context['sos_markers_data'] = sos_markers_data

    return render(request, 'administration/booking_map.html', context)

def api_kpis(request):
    today = timezone.localdate()
    start_7d = today - timedelta(days=6)

    active_users = UsersData.objects.exclude(status='BANNED').count()
    rides_today = Trip.objects.filter(trip_date=today).count()

    cancellations_today = (
        Booking.objects.filter(booking_status='CANCELLED', cancelled_at__date=today).count()
        + Trip.objects.filter(trip_status='CANCELLED', cancelled_at__date=today).count()
    )

    completed_trips_today = Trip.objects.filter(trip_status='COMPLETED', trip_date=today).count()

    avg_wait_duration = (
        Booking.objects
        .filter(pickup_verified_at__isnull=False, booked_at__date__gte=start_7d, booked_at__date__lte=today)
        .aggregate(
            avg=Avg(
                ExpressionWrapper(
                    F('pickup_verified_at') - F('booked_at'),
                    output_field=DurationField(),
                )
            )
        )
        .get('avg')
    )
    avg_wait_minutes = None
    if avg_wait_duration is not None:
        try:
            avg_wait_minutes = round(float(avg_wait_duration.total_seconds()) / 60.0, 2)
        except Exception:
            avg_wait_minutes = None

    data = {
        'active_users': active_users,
        'rides_today': rides_today,
        'cancellations': cancellations_today,
        'avg_wait_minutes': avg_wait_minutes,
        'completed_trips': completed_trips_today,
        'flagged_incidents': SosIncident.objects.filter(status='OPEN').count(),
    }
    return JsonResponse(data)


def sos_dashboard_view(request):
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return redirect('administration:login_view')

    open_incidents = (
        SosIncident.objects
        .select_related('actor', 'trip', 'booking')
        .filter(status='OPEN')
        .order_by('-created_at')[:200]
    )
    resolved_incidents = (
        SosIncident.objects
        .select_related('actor', 'trip', 'booking', 'resolved_by')
        .filter(status='RESOLVED')
        .order_by('-resolved_at', '-created_at')[:100]
    )

    return render(
        request,
        'administration/sos_dashboard.html',
        {
            'open_incidents': open_incidents,
            'resolved_incidents': resolved_incidents,
        },
    )


def sos_incident_detail_view(request, incident_id):
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return redirect('administration:login_view')

    incident = get_object_or_404(
        SosIncident.objects.select_related('actor', 'trip', 'booking', 'resolved_by'),
        pk=incident_id,
    )
    return render(
        request,
        'administration/sos_detail.html',
        {
            'incident': incident,
        },
    )


@require_http_methods(['POST'])
@csrf_protect
def sos_incident_resolve_view(request, incident_id):
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return redirect('administration:login_view')

    incident = get_object_or_404(SosIncident, pk=incident_id)
    if incident.status != SosIncident.STATUS_RESOLVED:
        incident.status = SosIncident.STATUS_RESOLVED
        incident.resolved_at = timezone.now()
        incident.resolved_by = request.user
        note = (request.POST.get('resolved_note') or '').strip()
        incident.resolved_note = note or None
        incident.save()

    return redirect('administration:sos_incident_detail', incident_id=incident.id)

def api_chart_data(request):
    today = timezone.localdate()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    day_labels = [d.strftime('%a') for d in days]

    completed_by_day = []
    active_drivers_by_day = []
    active_riders_by_day = []
    avg_wait_by_day = []

    for d in days:
        completed_by_day.append(
            Trip.objects.filter(trip_status='COMPLETED', trip_date=d).count()
        )
        active_drivers_by_day.append(
            Trip.objects.filter(trip_date=d).values('driver_id').distinct().count()
        )
        active_riders_by_day.append(
            Booking.objects.filter(booked_at__date=d).values('passenger_id').distinct().count()
        )

        avg_wait_duration = (
            Booking.objects
            .filter(pickup_verified_at__isnull=False, booked_at__date=d)
            .aggregate(
                avg=Avg(
                    ExpressionWrapper(
                        F('pickup_verified_at') - F('booked_at'),
                        output_field=DurationField(),
                    )
                )
            )
            .get('avg')
        )
        if avg_wait_duration is None:
            avg_wait_by_day.append(None)
        else:
            try:
                avg_wait_by_day.append(round(float(avg_wait_duration.total_seconds()) / 60.0, 2))
            except Exception:
                avg_wait_by_day.append(None)

    # bookings in last 24h bucketed into 4-hour windows
    now = timezone.now()
    since = now - timedelta(hours=24)
    hour_counts = (
        Booking.objects
        .filter(booked_at__gte=since)
        .annotate(h=ExtractHour('booked_at'))
        .values('h')
        .annotate(c=Count('id'))
    )
    hour_map = {row['h']: row['c'] for row in hour_counts if row.get('h') is not None}
    by_hour_labels = ['0h', '4h', '8h', '12h', '16h', '20h', '24h']
    by_hour = [0, 0, 0, 0, 0, 0, 0]
    for h, c in hour_map.items():
        try:
            idx = int(h) // 4
            if idx < 0:
                idx = 0
            if idx > 5:
                idx = 5
            by_hour[idx] += int(c)
        except Exception:
            pass

    # Cancellation breakdown (approximation based on available DB fields)
    cancelled_bookings_7d = Booking.objects.filter(booking_status='CANCELLED', cancelled_at__date__gte=days[0], cancelled_at__date__lte=days[-1]).count()
    cancelled_trips_7d = Trip.objects.filter(trip_status='CANCELLED', cancelled_at__date__gte=days[0], cancelled_at__date__lte=days[-1]).count()
    cancelled_safety_7d = Trip.objects.filter(trip_status='CANCELLED', cancelled_at__date__gte=days[0], cancelled_at__date__lte=days[-1], cancellation_reason__icontains='safety').count()
    other_cancellations_7d = max(cancelled_trips_7d - cancelled_safety_7d, 0)
    cancel_reasons = [
        cancelled_bookings_7d,
        cancelled_trips_7d,
        cancelled_safety_7d,
        other_cancellations_7d,
    ]

    return JsonResponse({
        'labels': day_labels,
        'tsRides': completed_by_day,
        'byHourLabels': by_hour_labels,
        'byHour': by_hour,
        'drivers': active_drivers_by_day,
        'riders': active_riders_by_day,
        'cancelReasons': cancel_reasons,
        'completedTrips': completed_by_day,
        'avgWait': avg_wait_by_day,
    })

def user_list_view(request):
    return render(request, 'administration/users_list.html')
# AJAX API: list users
def api_users(request):
    qs = UsersData.objects.all().values(
        'id','name','email','status','driver_rating','passenger_rating','created_at'
    )
    return JsonResponse({'users': list(qs)})

# --- User vehicles helpers and CRUD ---

def _vehicle_to_dict(v: Vehicle):
    return {
        'id': v.id,
        'model_number': v.model_number,
        'variant': v.variant,
        'company_name': v.company_name,
        'plate_number': v.plate_number,
        'vehicle_type': v.vehicle_type,
        'color': v.color,
        'photo_front_url': v.photo_front_url,
        'photo_back_url': v.photo_back_url,
        'documents_image_url': v.documents_image_url,
        'seats': v.seats,
        'engine_number': v.engine_number,
        'chassis_number': v.chassis_number,
        'fuel_type': v.fuel_type,
        'registration_date': v.registration_date.isoformat() if v.registration_date else None,
        'insurance_expiry': v.insurance_expiry.isoformat() if v.insurance_expiry else None,
        'created_at': v.created_at.isoformat() if hasattr(v, 'created_at') else None,
        'updated_at': v.updated_at.isoformat() if hasattr(v, 'updated_at') else None,
    }


def api_user_vehicles(request, user_id):
    """Return JSON list of vehicles for a given user (admin view)."""
    user = get_object_or_404(UsersData, pk=user_id)
    vehicles = user.vehicles.all().order_by('-created_at')
    data = [_vehicle_to_dict(v) for v in vehicles]
    return JsonResponse({'user_id': user.id, 'vehicles': data})


def user_vehicles_redirect_view(request, user_id):
    """Convenience URL that redirects to the user detail page where vehicles are listed."""
    return redirect('administration:user_detail', user_id=user_id)
# 2) Detail page
def user_detail_view(request, user_id):
    # api_user_detail(request, user_id)
    user = get_object_or_404(UsersData, pk=user_id)
    vehicles = user.vehicles.all().order_by('-created_at')
    emergency_contact = EmergencyContact.objects.filter(user=user).first()
    return render(
        request,
        'administration/users_detail.html',
        {
            'user_id': user_id,
            'user': user,
            'vehicles': vehicles,
            'emergency_contact': emergency_contact,
        },
    )
# AJAX API: detail JSON
def api_user_detail(request, user_id):
    user = get_object_or_404(UsersData, pk=user_id)
    data = {f: getattr(user, f) for f in [
        'id','name','username','email','address','phone_no','status','gender',
        'driver_rating','passenger_rating','cnic_no','driving_license_no',
        'accountno','bankname','iban','created_at','updated_at'
    ]}
    # Expose image URLs stored in UsersData (Supabase Storage paths)
    for img_url_field in [
        'profile_photo_url', 'live_photo_url',
        'cnic_front_image_url', 'cnic_back_image_url',
        'driving_license_front_url', 'driving_license_back_url',
        'accountqr_url',
    ]:
        data[img_url_field] = getattr(user, img_url_field, None)
    return JsonResponse(data)
# Update status via HTML form
@require_http_methods(['POST'])
def update_user_status_view(request, user_id):
    user = get_object_or_404(UsersData, pk=user_id)
    status = request.POST.get('status')
    if status in ['PENDING','VERIFIED','REJECTED','BANNED']:
        user.status = status
        if status == 'REJECTED':
            reason = (request.POST.get('rejection_reason') or '').strip()
            user.rejection_reason = reason or None
        else:
            user.rejection_reason = None
        user.save()
    return redirect('administration:user_detail', user_id=user_id)
# 3) Edit page HTML form
def user_edit_view(request, user_id):
    user = get_object_or_404(UsersData, pk=user_id)
    emergency_contact = EmergencyContact.objects.filter(user=user).first()
    return render(
        request,
        'administration/users_edit.html',
        {'user': user, 'user_id': user_id, 'emergency_contact': emergency_contact},
    )
# Handle edit form submission
@require_http_methods(['POST'])
def submit_user_edit(request, user_id):
    user = get_object_or_404(UsersData, pk=user_id)
    user.name = request.POST.get('name')
    user.username = request.POST.get('username')
    user.email = request.POST.get('email')
    password = request.POST.get('password')
    if password:
        user.password = make_password(password)
    user.address = request.POST.get('address')
    phone_no = request.POST.get('phone_no')
    # Ensure phone number has + prefix for international format
    if phone_no and not phone_no.startswith('+'):
        phone_no = '+' + phone_no
    user.phone_no = phone_no
    user.gender = request.POST.get('gender')
    user.status = request.POST.get('status')
    user.driver_rating = request.POST.get('driver_rating') or None
    user.passenger_rating = request.POST.get('passenger_rating') or None
    user.cnic_no = request.POST.get('cnic_no')
    user.driving_license_no = request.POST.get('driving_license_no')
    user.accountno = request.POST.get('accountno')
    user.iban = request.POST.get('iban')
    user.bankname = request.POST.get('bankname')
    # handle file uploads for all binary fields
    if request.FILES.get('accountqr'):
        user.accountqr = request.FILES['accountqr'].read()
    if request.FILES.get('profile_photo'):
        user.profile_photo = request.FILES['profile_photo'].read()
    if request.FILES.get('live_photo'):
        user.live_photo = request.FILES['live_photo'].read()
    if request.FILES.get('cnic_front_image'):
        user.cnic_front_image = request.FILES['cnic_front_image'].read()
    if request.FILES.get('cnic_back_image'):
        user.cnic_back_image = request.FILES['cnic_back_image'].read()
    if request.FILES.get('driving_license_front'):
        user.driving_license_front = request.FILES['driving_license_front'].read()
    if request.FILES.get('driving_license_back'):
        user.driving_license_back = request.FILES['driving_license_back'].read()
    try:
        emergency_name = (request.POST.get('emergency_name') or '').strip()
        emergency_relation = (request.POST.get('emergency_relation') or '').strip()
        emergency_email = (request.POST.get('emergency_email') or '').strip()
        emergency_phone_no = (request.POST.get('emergency_phone_no') or '').strip()
        ec = EmergencyContact.objects.filter(user=user).first()

        if any([emergency_name, emergency_relation, emergency_email, emergency_phone_no]):
            if ec is None:
                ec = EmergencyContact(user=user)
            ec.name = emergency_name
            ec.relation = emergency_relation
            ec.email = emergency_email
            ec.phone_no = emergency_phone_no
            ec.full_clean()

        user.full_clean()
        user.save()

        if any([emergency_name, emergency_relation, emergency_email, emergency_phone_no]):
            ec.save()
        else:
            if ec is not None:
                ec.delete()

        return redirect('administration:user_detail', user_id=user_id)
    except Exception as e:
        emergency_contact = EmergencyContact.objects.filter(user=user).first()
        return render(
            request,
            'administration/users_edit.html',
            {'user': user, 'user_id': user_id, 'error': str(e), 'emergency_contact': emergency_contact},
        )


def vehicle_detail_view(request, user_id):
    """Show a dedicated page listing all vehicles for a given user."""
    user = get_object_or_404(UsersData, pk=user_id)
    vehicles = user.vehicles.all().order_by('-created_at')
    return render(
        request,
        'administration/vehicle_detail.html',
        {
            'user': user,
            'vehicles': vehicles,
            'user_id': user_id,
        },
    )


@csrf_protect
def vehicle_add_view(request, user_id):
    user = get_object_or_404(UsersData, pk=user_id)
    if request.method == 'POST':
        v = Vehicle(owner=user)
        v.model_number = request.POST.get('model_number') or ''
        v.variant = request.POST.get('variant') or ''
        v.company_name = request.POST.get('company_name') or ''
        v.plate_number = request.POST.get('plate_number') or ''
        v.vehicle_type = request.POST.get('vehicle_type') or Vehicle.TWO_WHEELER
        v.color = request.POST.get('color') or ''
        seats_raw = request.POST.get('seats')
        v.seats = int(seats_raw) if seats_raw else None
        v.engine_number = request.POST.get('engine_number') or ''
        v.chassis_number = request.POST.get('chassis_number') or ''
        v.fuel_type = request.POST.get('fuel_type') or ''
        reg_date = request.POST.get('registration_date') or None
        ins_date = request.POST.get('insurance_expiry') or None
        from datetime import datetime
        if reg_date:
            try:
                v.registration_date = datetime.strptime(reg_date, '%Y-%m-%d').date()
            except ValueError:
                pass
        if ins_date:
            try:
                v.insurance_expiry = datetime.strptime(ins_date, '%Y-%m-%d').date()
            except ValueError:
                pass
        try:
            v.full_clean()
            v.save()
            return redirect('administration:user_detail', user_id=user_id)
        except Exception as e:
            return render(
                request,
                'administration/vehicle_edit.html',
                {'user': user, 'vehicle': v, 'user_id': user_id, 'error': str(e), 'is_new': True},
            )

    # GET: empty form
    return render(
        request,
        'administration/vehicle_edit.html',
        {'user': user, 'vehicle': None, 'user_id': user_id, 'is_new': True},
    )


@csrf_protect
def vehicle_edit_view(request, user_id, vehicle_id):
    user = get_object_or_404(UsersData, pk=user_id)
    vehicle = get_object_or_404(Vehicle, pk=vehicle_id, owner=user)
    if request.method == 'POST':
        vehicle.model_number = request.POST.get('model_number') or ''
        vehicle.variant = request.POST.get('variant') or ''
        vehicle.company_name = request.POST.get('company_name') or ''
        vehicle.plate_number = request.POST.get('plate_number') or ''
        vehicle.vehicle_type = request.POST.get('vehicle_type') or Vehicle.TWO_WHEELER
        vehicle.color = request.POST.get('color') or ''
        seats_raw = request.POST.get('seats')
        vehicle.seats = int(seats_raw) if seats_raw else None
        vehicle.engine_number = request.POST.get('engine_number') or ''
        vehicle.chassis_number = request.POST.get('chassis_number') or ''
        vehicle.fuel_type = request.POST.get('fuel_type') or ''
        reg_date = request.POST.get('registration_date') or None
        ins_date = request.POST.get('insurance_expiry') or None
        from datetime import datetime
        if reg_date:
            try:
                vehicle.registration_date = datetime.strptime(reg_date, '%Y-%m-%d').date()
            except ValueError:
                pass
        if ins_date:
            try:
                vehicle.insurance_expiry = datetime.strptime(ins_date, '%Y-%m-%d').date()
            except ValueError:
                pass
        try:
            vehicle.full_clean()
            vehicle.save()
            return redirect('administration:user_detail', user_id=user_id)
        except Exception as e:
            return render(
                request,
                'administration/vehicle_edit.html',
                {'user': user, 'vehicle': vehicle, 'user_id': user_id, 'error': str(e), 'is_new': False},
            )

    # GET: pre-filled form
    return render(
        request,
        'administration/vehicle_edit.html',
        {'user': user, 'vehicle': vehicle, 'user_id': user_id, 'is_new': False},
    )


@require_http_methods(['POST'])
@csrf_protect
def vehicle_delete_view(request, user_id, vehicle_id):
    user = get_object_or_404(UsersData, pk=user_id)
    vehicle = get_object_or_404(Vehicle, pk=vehicle_id, owner=user)
    vehicle.delete()
    return redirect('administration:user_detail', user_id=user_id)


@require_http_methods(['POST'])
@csrf_protect
def vehicle_update_status_view(request, user_id, vehicle_id):
    user = get_object_or_404(UsersData, pk=user_id)
    vehicle = get_object_or_404(Vehicle, pk=vehicle_id, owner=user)
    status = (request.POST.get('status') or '').strip().upper()
    if status not in [Vehicle.STATUS_PENDING, Vehicle.STATUS_VERIFIED, Vehicle.STATUS_REJECTED]:
        return redirect('administration:vehicle_detail', user_id=user_id)
    vehicle.status = status
    try:
        vehicle.full_clean()
    except Exception:
        pass
    vehicle.save(update_fields=['status', 'updated_at'])
    return redirect('administration:vehicle_detail', user_id=user_id)
@csrf_exempt
def login_view(request):
    error_message = ''
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        user_admin = authenticate(request, username=username, password=password)
        if user_admin is not None:
            login(request, user_admin)
            return redirect('administration:admin_view')
        else:
            error_message = 'Invalid credentials'
    return render(request, 'administration/login.html', {'error_message': error_message})
def logout_view(request):
    logout(request)
    return redirect('administration:login_view')
