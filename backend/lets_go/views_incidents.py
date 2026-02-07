import json
import os
import smtplib
from datetime import timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from django.http import JsonResponse
from django.urls import reverse
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils import timezone

from .models.models_trip import Trip, RideAuditEvent
from .models.models_booking import Booking
from .models.models_userdata import UsersData
from .models.models_emergency import EmergencyContact
from .models.models_incident import SosIncident, SosShareToken, TripShareToken


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
        return float(v)
    try:
        return float(str(v).strip())
    except Exception:
        return None


def _send_email(subject: str, body: str, recipients: list[str]) -> bool:
    sender_email = os.getenv("SENDER_EMAIL", "")
    sender_password = os.getenv("SENDER_PASSWORD", "")
    smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", 587))

    recipients = [r for r in recipients if isinstance(r, str) and r.strip()]
    if not recipients:
        return False

    message = MIMEMultipart()
    message["From"] = sender_email
    message["To"] = ",".join(recipients)
    message["Subject"] = subject
    message.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(smtp_server, smtp_port) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.send_message(message)
        return True
    except Exception:
        return False


def _parse_iso_dt(v):
    if not v:
        return None
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(v))
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt
    except Exception:
        return None


def _get_share_token(token: str):
    token = (token or '').strip()
    if not token:
        return None
    try:
        return SosShareToken.objects.select_related('incident', 'incident__trip', 'incident__booking').get(token=token)
    except SosShareToken.DoesNotExist:
        return None


def _get_trip_share_token(token: str):
    token = (token or '').strip()
    if not token:
        return None
    try:
        return TripShareToken.objects.select_related('trip', 'booking').get(token=token)
    except TripShareToken.DoesNotExist:
        return None


def _send_sms(phone_number: str, message: str) -> bool:
    base_url = os.getenv("TEXTBEE_BASE_URL", "https://api.textbee.dev")
    api_key = os.getenv("TEXTBEE_API_KEY", "")
    device_id = os.getenv("TEXTBEE_DEVICE_ID", "")

    if not phone_number or not message:
        return False

    if not phone_number.startswith("+"):
        phone_number = f"+{phone_number}"

    url = f"{base_url}/api/v1/gateway/devices/{device_id}/send-sms"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "recipients": [phone_number],
        "message": message,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=8)
        return 200 <= resp.status_code < 300
    except Exception:
        return False


@csrf_exempt
@require_http_methods(["POST"])
def sos_incident(request):
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    user_id = _coerce_int(data.get('user_id'))
    trip_id = (data.get('trip_id') or '').strip()
    role = (data.get('role') or '').strip().lower()
    booking_id = _coerce_int(data.get('booking_id'))
    lat = _coerce_float(data.get('lat'))
    lng = _coerce_float(data.get('lng'))
    accuracy = _coerce_float(data.get('accuracy'))
    note = (data.get('note') or '').strip()

    if not user_id:
        return JsonResponse({'success': False, 'error': 'user_id is required'}, status=400)
    if not trip_id:
        return JsonResponse({'success': False, 'error': 'trip_id is required'}, status=400)
    if role not in ['driver', 'passenger']:
        return JsonResponse({'success': False, 'error': 'role must be driver or passenger'}, status=400)
    if lat is None or lng is None:
        return JsonResponse({'success': False, 'error': 'lat and lng are required'}, status=400)

    try:
        actor = UsersData.objects.get(id=user_id)
    except UsersData.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)

    try:
        trip = Trip.objects.get(trip_id=trip_id)
    except Trip.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)

    booking = None
    if booking_id is not None:
        try:
            booking = Booking.objects.get(id=booking_id)
            if booking.trip_id != trip.id:
                booking = None
        except Booking.DoesNotExist:
            booking = None

    payload = {
        'trip_id': trip_id,
        'role': role,
        'lat': lat,
        'lng': lng,
        'accuracy': accuracy,
        'note': note,
        'server_time': timezone.now().isoformat(),
    }

    audit = RideAuditEvent.objects.create(
        trip=trip,
        booking=booking,
        actor=actor,
        event_type='SOS_TRIGGERED',
        payload=payload,
        created_at=timezone.now(),
    )

    incident = SosIncident.objects.create(
        trip=trip,
        booking=booking,
        actor=actor,
        audit_event=audit,
        role=role,
        latitude=lat,
        longitude=lng,
        accuracy=accuracy,
        note=note or None,
        status=SosIncident.STATUS_OPEN,
        created_at=timezone.now(),
    )

    expires_at = timezone.now() + timedelta(hours=6)
    share_token = SosShareToken.mint(incident=incident, expires_at=expires_at)
    share_url = request.build_absolute_uri(
        reverse('sos_share', kwargs={'token': share_token.token})
    )

    maps_url = f"https://www.google.com/maps?q={lat},{lng}"

    emergency = EmergencyContact.objects.filter(user=actor).first()

    admin_email = os.getenv('SOS_ADMIN_EMAIL', os.getenv('SENDER_EMAIL', 'letsgofyp@gmail.com'))
    subject = f"SOS Alert: {actor.name} ({role})"

    msg_lines = [
        'SOS ALERT',
        f"Time: {timezone.now().isoformat()}",
        f"User: {actor.name} (id={actor.id})",
        f"Role: {role}",
        f"Trip: {trip.trip_id}",
    ]
    if booking is not None:
        msg_lines.append(f"Booking: {booking.id}")
    msg_lines.extend([
        f"Location: {lat}, {lng}",
        f"Accuracy: {accuracy}",
    ])
    if note:
        msg_lines.append(f"Note: {note}")

    msg_lines.append(f"SOS tracking page: {share_url}")

    body = "\n".join([line for line in msg_lines if line is not None])

    email_to_contact_ok = False
    sms_to_contact_ok = False

    if emergency is not None:
        if emergency.email:
            email_to_contact_ok = _send_email(subject, body, [emergency.email])
            print(f"S.O.S email is :: subject :: {subject}\nbody :: {body}\nsend to {emergency.email}")
        if emergency.phone_no:
            sms_to_contact_ok = _send_sms(emergency.phone_no, body)
            print(f"S.O.S SMS is :: body :: {body}\nsend to {emergency.phone_no}")

    admin_email_ok = _send_email(subject, body, [admin_email])

    return JsonResponse({
        'success': True,
        'audit_event_id': audit.id,
        'incident_id': incident.id,
        'share_url': share_url,
        'maps_url': maps_url,
        'notified': {
            'emergency_contact_email': email_to_contact_ok,
            'emergency_contact_sms': sms_to_contact_ok,
            'admin_email': admin_email_ok,
        },
    })


@csrf_exempt
@require_http_methods(["POST"])
def trip_share_token(request, trip_id):
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except Exception:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)

    role = (data.get('role') or '').strip().lower()
    booking_id = _coerce_int(data.get('booking_id'))

    if not trip_id:
        return JsonResponse({'success': False, 'error': 'trip_id is required'}, status=400)
    if role not in ['driver', 'passenger']:
        return JsonResponse({'success': False, 'error': 'role must be driver or passenger'}, status=400)

    try:
        trip = Trip.objects.get(trip_id=trip_id)
    except Trip.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Trip not found'}, status=404)

    booking = None
    if role == 'passenger':
        if not booking_id:
            return JsonResponse({'success': False, 'error': 'booking_id is required for passenger share'}, status=400)
        try:
            booking = Booking.objects.get(id=booking_id)
            if booking.trip_id != trip.id:
                return JsonResponse({'success': False, 'error': 'Booking does not belong to trip'}, status=400)
        except Booking.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Booking not found'}, status=404)

    expires_at = timezone.now() + timedelta(hours=6)
    share_token = TripShareToken.mint(trip=trip, role=role, booking=booking, expires_at=expires_at)
    share_url = request.build_absolute_uri(
        reverse('trip_share', kwargs={'token': share_token.token})
    )

    return JsonResponse({'success': True, 'share_url': share_url})


@require_http_methods(["GET"])
def trip_share_view(request, token):
    share = _get_trip_share_token(token)
    if share is None or not share.is_active():
        return JsonResponse({'success': False, 'error': 'Invalid or expired link'}, status=404)

    trip = share.trip

    trip_meta_data = {
        'trip_id': getattr(trip, 'trip_id', None),
        'share_live_url': request.build_absolute_uri(
            reverse('trip_share_live', kwargs={'token': share.token})
        ),
    }

    trip_public_data = {
        'trip_id': getattr(trip, 'trip_id', None),
        'route_name': getattr(getattr(trip, 'route', None), 'route_name', None),
        'trip_date': trip.trip_date.isoformat() if getattr(trip, 'trip_date', None) else None,
        'departure_time': trip.departure_time.strftime('%H:%M') if getattr(trip, 'departure_time', None) else None,
        'estimated_arrival_time': trip.estimated_arrival_time.strftime('%H:%M') if getattr(trip, 'estimated_arrival_time', None) else None,
        'vehicle': {
            'type': getattr(getattr(trip, 'vehicle', None), 'vehicle_type', None),
            'plate_masked': None,
        },
    }

    try:
        plate = getattr(getattr(trip, 'vehicle', None), 'plate_number', None)
        if isinstance(plate, str) and plate.strip():
            plate = plate.strip()
            trip_public_data['vehicle']['plate_masked'] = (plate[:3] + '***' + plate[-3:]) if len(plate) > 6 else ('***' + plate[-3:])
    except Exception:
        pass

    route_stops_data = []
    route_geometry_data = []
    try:
        route = getattr(trip, 'route', None)
        if route is not None:
            for rs in route.route_stops.all().order_by('stop_order'):
                if getattr(rs, 'latitude', None) is not None and getattr(rs, 'longitude', None) is not None:
                    route_stops_data.append({
                        'name': getattr(rs, 'stop_name', None),
                        'order': getattr(rs, 'stop_order', None),
                        'lat': float(rs.latitude),
                        'lng': float(rs.longitude),
                    })

            geom = getattr(route, 'route_geometry', None) or []
            if isinstance(geom, list):
                for p in geom:
                    try:
                        if isinstance(p, dict) and 'lat' in p and 'lng' in p:
                            route_geometry_data.append({'lat': float(p['lat']), 'lng': float(p['lng'])})
                    except Exception:
                        pass
    except Exception:
        route_stops_data = []
        route_geometry_data = []

    driver_path_data = []
    try:
        st = trip.live_tracking_state or {}
        path = st.get('driver_path') if isinstance(st, dict) else None
        if isinstance(path, list):
            tail = path[-300:] if len(path) > 300 else path
            for p in tail:
                if not isinstance(p, dict):
                    continue
                if p.get('lat') is None or p.get('lng') is None:
                    continue
                driver_path_data.append({'lat': float(p.get('lat')), 'lng': float(p.get('lng'))})
    except Exception:
        driver_path_data = []

    return render(
        request,
        'administration/trip_share_public.html',
        {
            'token': share,
            'trip_meta_data': trip_meta_data,
            'trip_public_data': trip_public_data,
            'route_stops_data': route_stops_data,
            'route_geometry_data': route_geometry_data,
            'driver_path_data': driver_path_data,
        },
    )


@require_http_methods(["GET"])
def trip_share_live(request, token):
    share = _get_trip_share_token(token)
    if share is None or not share.is_active():
        return JsonResponse({'success': False, 'error': 'Invalid or expired link'}, status=404)

    trip = share.trip
    role = (share.role or '').strip().lower()

    live_state = trip.live_tracking_state or {}
    actor = None
    ts = None
    speed_kph = None
    driver_path = None

    if isinstance(live_state, dict):
        try:
            dp = live_state.get('driver_path')
            if isinstance(dp, list):
                tail = dp[-300:] if len(dp) > 300 else dp
                driver_path = []
                for p in tail:
                    if not isinstance(p, dict):
                        continue
                    if p.get('lat') is None or p.get('lng') is None:
                        continue
                    driver_path.append({'lat': float(p.get('lat')), 'lng': float(p.get('lng'))})
        except Exception:
            driver_path = None

        if role == 'driver':
            drv = live_state.get('driver')
            if isinstance(drv, dict) and drv.get('lat') is not None and drv.get('lng') is not None:
                actor = {
                    'lat': float(drv.get('lat')),
                    'lng': float(drv.get('lng')),
                    'speed': _coerce_float(drv.get('speed')),
                    'timestamp': drv.get('timestamp'),
                }
                ts = _parse_iso_dt(drv.get('timestamp'))
                try:
                    if actor.get('speed') is not None:
                        speed_kph = float(actor.get('speed')) * 3.6
                except Exception:
                    speed_kph = None
        else:
            bid = getattr(share, 'booking_id', None)
            passengers = live_state.get('passengers')
            if bid is not None and isinstance(passengers, list):
                for p in passengers:
                    if not isinstance(p, dict):
                        continue
                    if p.get('booking_id') == bid and p.get('lat') is not None and p.get('lng') is not None:
                        actor = {
                            'lat': float(p.get('lat')),
                            'lng': float(p.get('lng')),
                            'speed': _coerce_float(p.get('speed')),
                            'timestamp': p.get('timestamp'),
                        }
                        ts = _parse_iso_dt(p.get('timestamp'))
                        try:
                            if actor.get('speed') is not None:
                                speed_kph = float(actor.get('speed')) * 3.6
                        except Exception:
                            speed_kph = None
                        break

    last_seen_seconds = None
    if ts is not None:
        try:
            last_seen_seconds = max(int((timezone.now() - ts).total_seconds()), 0)
        except Exception:
            last_seen_seconds = None

    return JsonResponse({
        'success': True,
        'trip_id': trip.trip_id,
        'live_state': {
            'actor': actor,
            'driver_path': driver_path,
        },
        'runtime': {
            'last_seen_seconds': last_seen_seconds,
            'speed_kph': speed_kph,
        },
    })


@require_http_methods(["GET"])
def sos_share_view(request, token):
    share = _get_share_token(token)
    if share is None or not share.is_active():
        return JsonResponse({'success': False, 'error': 'Invalid or expired link'}, status=404)

    incident = share.incident
    trip = incident.trip

    incident_data = {
        'id': incident.id,
        'role': incident.role,
        'lat': float(incident.latitude),
        'lng': float(incident.longitude),
        'note': incident.note,
        'created_at': incident.created_at.isoformat() if incident.created_at else None,
    }

    trip_meta_data = {
        'trip_id': getattr(trip, 'trip_id', None),
        'share_live_url': request.build_absolute_uri(
            reverse('sos_share_live', kwargs={'token': share.token})
        ),
    }

    trip_public_data = {
        'trip_id': getattr(trip, 'trip_id', None),
        'route_name': getattr(getattr(trip, 'route', None), 'route_name', None),
        'trip_date': trip.trip_date.isoformat() if getattr(trip, 'trip_date', None) else None,
        'departure_time': trip.departure_time.strftime('%H:%M') if getattr(trip, 'departure_time', None) else None,
        'estimated_arrival_time': trip.estimated_arrival_time.strftime('%H:%M') if getattr(trip, 'estimated_arrival_time', None) else None,
        'triggered_by': getattr(getattr(incident, 'actor', None), 'name', None),
        'vehicle': {
            'type': getattr(getattr(trip, 'vehicle', None), 'vehicle_type', None),
            'plate_masked': None,
        },
    }

    try:
        plate = getattr(getattr(trip, 'vehicle', None), 'plate_number', None)
        if isinstance(plate, str) and plate.strip():
            plate = plate.strip()
            trip_public_data['vehicle']['plate_masked'] = (plate[:3] + '***' + plate[-3:]) if len(plate) > 6 else ('***' + plate[-3:])
    except Exception:
        pass

    route_stops_data = []
    route_geometry_data = []
    try:
        route = getattr(trip, 'route', None)
        if route is not None:
            for rs in route.route_stops.all().order_by('stop_order'):
                if getattr(rs, 'latitude', None) is not None and getattr(rs, 'longitude', None) is not None:
                    route_stops_data.append({
                        'name': getattr(rs, 'stop_name', None),
                        'order': getattr(rs, 'stop_order', None),
                        'lat': float(rs.latitude),
                        'lng': float(rs.longitude),
                    })

            geom = getattr(route, 'route_geometry', None) or []
            if isinstance(geom, list):
                for p in geom:
                    try:
                        if isinstance(p, dict) and 'lat' in p and 'lng' in p:
                            route_geometry_data.append({'lat': float(p['lat']), 'lng': float(p['lng'])})
                    except Exception:
                        pass
    except Exception:
        route_stops_data = []
        route_geometry_data = []

    driver_path_data = []
    try:
        st = trip.live_tracking_state or {}
        path = st.get('driver_path') if isinstance(st, dict) else None
        if isinstance(path, list):
            tail = path[-300:] if len(path) > 300 else path
            for p in tail:
                if not isinstance(p, dict):
                    continue
                if p.get('lat') is None or p.get('lng') is None:
                    continue
                driver_path_data.append({'lat': float(p.get('lat')), 'lng': float(p.get('lng'))})
    except Exception:
        driver_path_data = []

    return render(
        request,
        'administration/sos_share_public.html',
        {
            'incident': incident,
            'token': share,
            'incident_data': incident_data,
            'trip_meta_data': trip_meta_data,
            'trip_public_data': trip_public_data,
            'route_stops_data': route_stops_data,
            'route_geometry_data': route_geometry_data,
            'driver_path_data': driver_path_data,
        },
    )


@require_http_methods(["GET"])
def sos_share_live(request, token):
    share = _get_share_token(token)
    if share is None or not share.is_active():
        return JsonResponse({'success': False, 'error': 'Invalid or expired link'}, status=404)

    incident = share.incident
    trip = incident.trip
    role = (incident.role or '').strip().lower()

    live_state = trip.live_tracking_state or {}
    actor = None
    ts = None
    speed_kph = None
    driver_path = None

    if isinstance(live_state, dict):
        try:
            dp = live_state.get('driver_path')
            if isinstance(dp, list):
                tail = dp[-300:] if len(dp) > 300 else dp
                driver_path = []
                for p in tail:
                    if not isinstance(p, dict):
                        continue
                    if p.get('lat') is None or p.get('lng') is None:
                        continue
                    driver_path.append({'lat': float(p.get('lat')), 'lng': float(p.get('lng'))})
        except Exception:
            driver_path = None

        if role == 'driver':
            drv = live_state.get('driver')
            if isinstance(drv, dict) and drv.get('lat') is not None and drv.get('lng') is not None:
                actor = {
                    'lat': float(drv.get('lat')),
                    'lng': float(drv.get('lng')),
                    'speed': _coerce_float(drv.get('speed')),
                    'timestamp': drv.get('timestamp'),
                }
                ts = _parse_iso_dt(drv.get('timestamp'))
                try:
                    if actor.get('speed') is not None:
                        speed_kph = float(actor.get('speed')) * 3.6
                except Exception:
                    speed_kph = None
        else:
            bid = getattr(incident, 'booking_id', None)
            passengers = live_state.get('passengers')
            if bid is not None and isinstance(passengers, list):
                for p in passengers:
                    if not isinstance(p, dict):
                        continue
                    if p.get('booking_id') == bid and p.get('lat') is not None and p.get('lng') is not None:
                        actor = {
                            'lat': float(p.get('lat')),
                            'lng': float(p.get('lng')),
                            'speed': _coerce_float(p.get('speed')),
                            'timestamp': p.get('timestamp'),
                        }
                        ts = _parse_iso_dt(p.get('timestamp'))
                        try:
                            if actor.get('speed') is not None:
                                speed_kph = float(actor.get('speed')) * 3.6
                        except Exception:
                            speed_kph = None
                        break

    last_seen_seconds = None
    if ts is not None:
        try:
            last_seen_seconds = max(int((timezone.now() - ts).total_seconds()), 0)
        except Exception:
            last_seen_seconds = None

    return JsonResponse({
        'success': True,
        'incident_id': incident.id,
        'trip_id': trip.trip_id,
        'live_state': {
            'actor': actor,
            'driver_path': driver_path,
        },
        'runtime': {
            'last_seen_seconds': last_seen_seconds,
            'speed_kph': speed_kph,
        },
    })


@require_http_methods(["GET"])
def sos_share_send(request, token):
    return redirect(reverse('sos_share', kwargs={'token': token}))
