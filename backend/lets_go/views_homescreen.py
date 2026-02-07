from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.db.models import Prefetch, Q
from django.utils import timezone
from datetime import datetime
import math
import re
import difflib

from .models import Trip, RouteStop, TripStopBreakdown, Booking, BlockedUser


def _to_int(value):
    try:
        return int(value)
    except Exception:
        return None


def _to_float(value):
    try:
        return float(value)
    except Exception:
        return None


def _normalize_text(value: str) -> str:
    v = (value or '').strip().lower()
    v = re.sub(r'[^a-z0-9\s]+', ' ', v)
    v = re.sub(r'\s+', ' ', v).strip()
    return v


def _haversine_meters(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * (math.sin(dl / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def _absolute_url(request, value):
    try:
        if value is None:
            return None
        if hasattr(value, 'url'):
            value = value.url
        s = str(value).strip()
        if not s:
            return None
        if s.startswith('http://') or s.startswith('https://'):
            return s
        return request.build_absolute_uri(s)
    except Exception:
        return None


def _vehicle_front_photo_url(request, vehicle):
    try:
        if vehicle is None:
            return None
        raw = getattr(vehicle, 'photo_front_url', None)
        if raw in (None, ''):
            raw = getattr(vehicle, 'photo_front', None)
        return _absolute_url(request, raw)
    except Exception:
        return None


def _fuzzy_score(query_norm: str, candidate_norm: str) -> float:
    if not query_norm:
        return 0.0
    if not candidate_norm:
        return 0.0
    if candidate_norm == query_norm:
        return 1.0
    if query_norm in candidate_norm:
        return 0.95
    return difflib.SequenceMatcher(None, query_norm, candidate_norm).ratio()


def _stop_order_matches(
    stops,
    q_from: str,
    q_to: str,
    from_stop_id: int | None = None,
    to_stop_id: int | None = None,
) -> bool:
    if (not q_from and not from_stop_id) or (not q_to and not to_stop_id):
        return True

    qf = (q_from or '').strip().lower()
    qt = (q_to or '').strip().lower()

    from_orders = []
    to_orders = []
    for s in stops:
        sid = getattr(s, 'id', None)
        sorder = getattr(s, 'stop_order', None)
        name = (getattr(s, 'stop_name', None) or '').lower()

        if from_stop_id and sid == from_stop_id:
            from_orders.append(sorder)
        elif qf and qf in name:
            from_orders.append(sorder)

        if to_stop_id and sid == to_stop_id:
            to_orders.append(sorder)
        elif qt and qt in name:
            to_orders.append(sorder)

    from_orders = [o for o in from_orders if isinstance(o, int)]
    to_orders = [o for o in to_orders if isinstance(o, int)]
    if not from_orders or not to_orders:
        return False

    for fo in from_orders:
        for to in to_orders:
            if fo < to:
                return True
    return False


@csrf_exempt
def suggest_stops(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Invalid request method'}, status=400)

    try:
        q = (request.GET.get('q') or '').strip()
        q_norm = _normalize_text(q)
        lat = _to_float(request.GET.get('lat'))
        lng = _to_float(request.GET.get('lng'))

        radius_km = _to_float(request.GET.get('radius_km'))
        if radius_km is None or radius_km <= 0 or radius_km > 200:
            radius_km = 10.0

        limit = _to_int(request.GET.get('limit'))
        if limit is None or limit <= 0 or limit > 50:
            limit = 12

        qs = RouteStop.objects.filter(is_active=True, route__is_active=True)

        if lat is not None and lng is not None:
            lat_delta = radius_km / 111.0
            cos_lat = math.cos(math.radians(lat))
            if cos_lat < 0.000001:
                cos_lat = 0.000001
            lng_delta = radius_km / (111.0 * cos_lat)

            qs = qs.exclude(latitude__isnull=True).exclude(longitude__isnull=True)
            qs = qs.filter(
                latitude__gte=lat - lat_delta,
                latitude__lte=lat + lat_delta,
                longitude__gte=lng - lng_delta,
                longitude__lte=lng + lng_delta,
            )

        qs = qs.select_related('route').only(
            'id',
            'stop_name',
            'stop_order',
            'latitude',
            'longitude',
            'route__route_id',
            'route__route_name',
        )

        candidates = []
        for s in qs[:2000]:
            s_lat = float(s.latitude) if s.latitude is not None else None
            s_lng = float(s.longitude) if s.longitude is not None else None

            dist_m = None
            if lat is not None and lng is not None and s_lat is not None and s_lng is not None:
                dist_m = _haversine_meters(lat, lng, s_lat, s_lng)

            name_norm = _normalize_text(s.stop_name)
            score = _fuzzy_score(q_norm, name_norm) if q_norm else 0.0

            if q_norm and score < 0.45:
                continue

            candidates.append({
                'id': s.id,
                'stop_name': s.stop_name,
                'stop_order': s.stop_order,
                'route_id': getattr(s.route, 'route_id', None),
                'route_name': getattr(s.route, 'route_name', None),
                'latitude': s_lat,
                'longitude': s_lng,
                'distance_m': dist_m,
                'score': score,
            })

        if q_norm and (lat is not None and lng is not None):
            candidates.sort(key=lambda x: (-x['score'], x['distance_m'] if x['distance_m'] is not None else 10**18))
        elif q_norm:
            candidates.sort(key=lambda x: -x['score'])
        elif lat is not None and lng is not None:
            candidates.sort(key=lambda x: x['distance_m'] if x['distance_m'] is not None else 10**18)
        else:
            candidates.sort(key=lambda x: _normalize_text(x['stop_name']))

        return JsonResponse({'success': True, 'stops': candidates[:limit]})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def all_trips(request):
    if request.method == 'GET':
        try:
            user_id = _to_int(request.GET.get('user_id'))

            try:
                limit = int(request.GET.get('limit', 50))
                limit = max(1, min(limit, 200))
            except Exception:
                limit = 50
            try:
                offset = int(request.GET.get('offset', 0))
                offset = max(0, offset)
            except Exception:
                offset = 0

            stop_breakdowns_prefetch = Prefetch(
                'stop_breakdowns',
                queryset=TripStopBreakdown.objects.only(
                    'trip_id', 'from_stop_order', 'to_stop_order', 'from_stop_name', 'to_stop_name',
                    'distance_km', 'duration_minutes', 'price',
                    'from_latitude', 'from_longitude', 'to_latitude', 'to_longitude', 'price_breakdown'
                ).order_by('from_stop_order')
            )

            route_stops_prefetch = Prefetch(
                'route__route_stops',
                queryset=RouteStop.objects.only('route_id', 'stop_order', 'stop_name').order_by('stop_order')
            )

            now = timezone.now()
            today = now.date()
            now_time = now.time()

            trips_qs = (
                Trip.objects.filter(
                    trip_status='SCHEDULED',
                    available_seats__gt=0,
                    started_at__isnull=True,
                )
                .filter(Q(trip_date__gt=today) | Q(trip_date=today, departure_time__gt=now_time))
                .select_related('route', 'driver', 'vehicle')
                .only(
                    'trip_id', 'trip_date', 'departure_time', 'estimated_arrival_time', 'available_seats',
                    'base_fare', 'gender_preference', 'total_seats', 'notes', 'is_negotiable',
                    'total_distance_km', 'total_duration_minutes', 'fare_calculation',
                    'route__route_name',
                    'driver__id', 'driver__name', 'driver__profile_photo_url',
                    'vehicle__company_name', 'vehicle__model_number', 'vehicle__photo_front_url'
                )
                .prefetch_related(stop_breakdowns_prefetch, route_stops_prefetch)
                .order_by('-trip_date', '-departure_time')
            )

            if user_id:
                trips_qs = trips_qs.exclude(driver_id=user_id)

                blocked_driver_ids = BlockedUser.objects.filter(blocker_id=user_id).values_list('blocked_user_id', flat=True)
                blocked_by_driver_ids = BlockedUser.objects.filter(blocked_user_id=user_id).values_list('blocker_id', flat=True)
                trips_qs = trips_qs.exclude(driver_id__in=blocked_driver_ids).exclude(driver_id__in=blocked_by_driver_ids)

                trips_qs = trips_qs.exclude(
                    trip_bookings__passenger_id=user_id,
                    trip_bookings__booking_status__in=['PENDING', 'CONFIRMED', 'COMPLETED'],
                ).exclude(
                    trip_bookings__passenger_id=user_id,
                    trip_bookings__blocked=True,
                )

            trips_qs = trips_qs.distinct()

            trips_qs = trips_qs[offset:offset + limit]

            trip_list = []
            for trip in trips_qs:
                route = trip.route
                driver = trip.driver
                vehicle = trip.vehicle

                origin_name = route.route_name
                destination_name = route.route_name
                try:
                    stops = list(route.route_stops.all())
                    if stops:
                        origin_name = stops[0].stop_name or origin_name
                        destination_name = stops[-1].stop_name or destination_name
                except Exception:
                    pass

                breakdown_list = []
                for breakdown in trip.stop_breakdowns.all():
                    breakdown_list.append({
                        'from_stop_order': breakdown.from_stop_order,
                        'to_stop_order': breakdown.to_stop_order,
                        'from_stop_name': breakdown.from_stop_name,
                        'to_stop_name': breakdown.to_stop_name,
                        'distance_km': float(breakdown.distance_km) if breakdown.distance_km is not None else None,
                        'duration_minutes': breakdown.duration_minutes,
                        'price': int(breakdown.price) if breakdown.price is not None else None,
                        'from_coordinates': {
                            'lat': float(breakdown.from_latitude) if breakdown.from_latitude is not None else None,
                            'lng': float(breakdown.from_longitude) if breakdown.from_longitude is not None else None,
                        },
                        'to_coordinates': {
                            'lat': float(breakdown.to_latitude) if breakdown.to_latitude is not None else None,
                            'lng': float(breakdown.to_longitude) if breakdown.to_longitude is not None else None,
                        },
                        'price_breakdown': breakdown.price_breakdown,
                    })

                trip_list.append({
                    'trip_id': trip.trip_id,
                    'departure_time': f"{trip.trip_date}T{trip.departure_time}",
                    'origin': origin_name,
                    'destination': destination_name,
                    'driver_name': driver.name if driver else None,
                    'driver_profile_photo_url': getattr(driver, 'profile_photo_url', None) if driver else None,
                    'vehicle_model': f"{vehicle.company_name} {vehicle.model_number}" if vehicle else 'Unknown Vehicle',
                    'vehicle_photo_front': _vehicle_front_photo_url(request, vehicle),
                    'available_seats': trip.available_seats,
                    'price_per_seat': int(trip.base_fare) if trip.base_fare is not None else None,
                    'gender_preference': trip.gender_preference,
                    'total_seats': trip.total_seats,
                    'estimated_arrival_time': str(trip.estimated_arrival_time) if trip.estimated_arrival_time else None,
                    'notes': trip.notes,
                    'is_negotiable': trip.is_negotiable,
                    'total_distance_km': float(trip.total_distance_km) if trip.total_distance_km is not None else None,
                    'total_duration_minutes': trip.total_duration_minutes,
                    'fare_calculation': trip.fare_calculation,
                    'stop_breakdown': breakdown_list,
                })

            return JsonResponse({'success': True, 'trips': trip_list})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)}, status=500)
    return JsonResponse({'error': 'Invalid request method'}, status=400)


@csrf_exempt
def search_trips(request):
    if request.method != 'GET':
        return JsonResponse({'error': 'Invalid request method'}, status=400)

    try:
        user_id = _to_int(request.GET.get('user_id'))
        from_stop_id = _to_int(request.GET.get('from_stop_id'))
        to_stop_id = _to_int(request.GET.get('to_stop_id'))
        q_from = (request.GET.get('from') or request.GET.get('origin') or '').strip()
        q_to = (request.GET.get('to') or request.GET.get('destination') or '').strip()
        date_str = (request.GET.get('date') or '').strip()
        min_seats_raw = (request.GET.get('min_seats') or request.GET.get('seats') or '').strip()
        max_price_raw = (request.GET.get('max_price') or '').strip()
        gender_pref = (request.GET.get('gender_preference') or '').strip()
        negotiable_raw = (request.GET.get('negotiable') or request.GET.get('negotiation_allowed') or '').strip()
        time_from_raw = (request.GET.get('time_from') or '').strip()
        time_to_raw = (request.GET.get('time_to') or '').strip()
        sort = (request.GET.get('sort') or '').strip().lower()

        try:
            limit = int(request.GET.get('limit', 50))
            limit = max(1, min(limit, 200))
        except Exception:
            limit = 50
        try:
            offset = int(request.GET.get('offset', 0))
            offset = max(0, offset)
        except Exception:
            offset = 0

        now = timezone.now()
        today = now.date()
        now_time = now.time()

        trips = Trip.objects.filter(
            trip_status='SCHEDULED',
            available_seats__gt=0,
            started_at__isnull=True,
        ).filter(Q(trip_date__gt=today) | Q(trip_date=today, departure_time__gt=now_time))

        if user_id:
            trips = trips.exclude(driver_id=user_id)

            blocked_driver_ids = BlockedUser.objects.filter(blocker_id=user_id).values_list('blocked_user_id', flat=True)
            blocked_by_driver_ids = BlockedUser.objects.filter(blocked_user_id=user_id).values_list('blocker_id', flat=True)
            trips = trips.exclude(driver_id__in=blocked_driver_ids).exclude(driver_id__in=blocked_by_driver_ids)

            trips = trips.exclude(
                trip_bookings__passenger_id=user_id,
                trip_bookings__booking_status__in=['PENDING', 'CONFIRMED', 'COMPLETED'],
            ).exclude(
                trip_bookings__passenger_id=user_id,
                trip_bookings__blocked=True,
            )

        if from_stop_id:
            trips = trips.filter(route__route_stops__id=from_stop_id)
        elif q_from:
            trips = trips.filter(route__route_stops__stop_name__icontains=q_from)
        if to_stop_id:
            trips = trips.filter(route__route_stops__id=to_stop_id)
        elif q_to:
            trips = trips.filter(route__route_stops__stop_name__icontains=q_to)

        if date_str:
            try:
                trip_date = datetime.strptime(date_str, '%Y-%m-%d').date()
                trips = trips.filter(trip_date=trip_date)
            except ValueError:
                return JsonResponse({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=400)

        if min_seats_raw:
            try:
                trips = trips.filter(available_seats__gte=int(min_seats_raw))
            except (TypeError, ValueError):
                return JsonResponse({'success': False, 'error': 'min_seats must be an integer.'}, status=400)

        if max_price_raw:
            try:
                trips = trips.filter(base_fare__lte=int(round(float(max_price_raw))))
            except (TypeError, ValueError):
                return JsonResponse({'success': False, 'error': 'max_price must be numeric.'}, status=400)

        if gender_pref:
            gender_norm = gender_pref.strip().capitalize()
            if gender_norm not in ['Male', 'Female', 'Any']:
                return JsonResponse({'success': False, 'error': 'gender_preference must be Male, Female, or Any.'}, status=400)
            trips = trips.filter(gender_preference=gender_norm)

        if negotiable_raw:
            neg_lower = negotiable_raw.lower()
            if neg_lower in ['1', 'true', 'yes']:
                trips = trips.filter(is_negotiable=True)
            elif neg_lower in ['0', 'false', 'no']:
                trips = trips.filter(is_negotiable=False)
            else:
                return JsonResponse({'success': False, 'error': 'negotiable must be true/false.'}, status=400)

        if time_from_raw:
            try:
                tf = datetime.strptime(time_from_raw, '%H:%M').time()
                trips = trips.filter(departure_time__gte=tf)
            except ValueError:
                return JsonResponse({'success': False, 'error': 'time_from must be HH:MM.'}, status=400)

        if time_to_raw:
            try:
                tt = datetime.strptime(time_to_raw, '%H:%M').time()
                trips = trips.filter(departure_time__lte=tt)
            except ValueError:
                return JsonResponse({'success': False, 'error': 'time_to must be HH:MM.'}, status=400)

        trips = trips.distinct()

        if sort == 'soonest':
            trips = trips.order_by('trip_date', 'departure_time')
        elif sort == 'latest':
            trips = trips.order_by('-trip_date', '-departure_time')
        elif sort == 'price_asc':
            trips = trips.order_by('base_fare', 'trip_date', 'departure_time')
        elif sort == 'price_desc':
            trips = trips.order_by('-base_fare', 'trip_date', 'departure_time')
        elif sort == 'seats_desc':
            trips = trips.order_by('-available_seats', 'trip_date', 'departure_time')
        elif sort:
            return JsonResponse({'success': False, 'error': 'Invalid sort.'}, status=400)
        else:
            trips = trips.order_by('trip_date', 'departure_time')

        route_stops_prefetch = Prefetch(
            'route__route_stops',
            queryset=RouteStop.objects.only('id', 'route_id', 'stop_order', 'stop_name').order_by('stop_order')
        )

        trips_qs = (
            trips
            .select_related('route', 'driver', 'vehicle')
            .only(
                'trip_id', 'trip_date', 'departure_time', 'estimated_arrival_time', 'available_seats',
                'base_fare', 'gender_preference', 'total_seats', 'notes', 'is_negotiable',
                'total_distance_km', 'total_duration_minutes', 'fare_calculation',
                'route__route_name',
                'driver__id', 'driver__name', 'driver__profile_photo_url',
                'vehicle__company_name', 'vehicle__model_number', 'vehicle__photo_front_url'
            )
            .prefetch_related(route_stops_prefetch)
        )

        trips_qs = trips_qs.distinct()

        fetch_n = min(1000, offset + limit + 300)
        trips_qs = trips_qs[:fetch_n]

        items = []
        for trip in trips_qs:
            route = trip.route
            driver = trip.driver
            vehicle = trip.vehicle

            origin_name = route.route_name if route else None
            destination_name = route.route_name if route else None

            try:
                if route is not None:
                    stops = list(route.route_stops.all())
                    if stops:
                        origin_name = stops[0].stop_name or origin_name
                        destination_name = stops[-1].stop_name or destination_name
                        if (q_from or from_stop_id) and (q_to or to_stop_id) and not _stop_order_matches(
                            stops,
                            q_from,
                            q_to,
                            from_stop_id=from_stop_id,
                            to_stop_id=to_stop_id,
                        ):
                            continue
            except Exception:
                pass

            items.append({
                'trip_id': trip.trip_id,
                'departure_time': f"{trip.trip_date}T{trip.departure_time}",
                'origin': origin_name,
                'destination': destination_name,
                'driver_name': driver.name if driver else None,
                'driver_profile_photo_url': getattr(driver, 'profile_photo_url', None) if driver else None,
                'vehicle_model': f"{vehicle.company_name} {vehicle.model_number}" if vehicle else 'Unknown Vehicle',
                'vehicle_photo_front': _vehicle_front_photo_url(request, vehicle),
                'available_seats': trip.available_seats,
                'price_per_seat': int(trip.base_fare) if trip.base_fare is not None else None,
                'gender_preference': trip.gender_preference,
                'total_seats': trip.total_seats,
                'estimated_arrival_time': str(trip.estimated_arrival_time) if trip.estimated_arrival_time else None,
                'notes': trip.notes,
                'is_negotiable': trip.is_negotiable,
                'total_distance_km': float(trip.total_distance_km) if trip.total_distance_km is not None else None,
                'total_duration_minutes': trip.total_duration_minutes,
                'fare_calculation': trip.fare_calculation,
            })

        trip_list = items[offset:offset + limit]

        return JsonResponse({
            'success': True,
            'trips': trip_list,
            'meta': {
                'limit': limit,
                'offset': offset,
            },
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
