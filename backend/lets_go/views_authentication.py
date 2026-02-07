from django.shortcuts import render
from django.http import JsonResponse, HttpResponse, Http404
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.hashers import make_password, check_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.utils import OperationalError
from django.utils import timezone
from django.conf import settings
from datetime import datetime, timedelta, time
import time as pytime
from decimal import Decimal
import json
import random
import string
import base64
import requests
from .models import UsersData, Vehicle, Trip, Route, RouteStop, TripStopBreakdown, Booking, EmergencyContact, UsernameRegistry, ChangeRequest
from django.views.decorators.http import require_GET
# from .utils.fare_calculator import is_peak_hour, get_fare_matrix_for_route
from .email_otp import send_email_otp, send_email_otp_for_reset
from .phone_otp_send import send_phone_otp, send_phone_otp_for_reset
from .constants import url


def upload_to_supabase(bucket_name, file_obj, dest_path):
    """Upload a file-like object to Supabase Storage and return its public URL.

    Uses SUPABASE_URL and SUPABASE_SERVICE_KEY from settings. Assumes bucket is public.
    """
    supabase_url = getattr(settings, "SUPABASE_URL", "").rstrip("/")
    service_key = getattr(settings, "SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not service_key:
        raise RuntimeError("Supabase configuration missing in settings.")

    upload_url = f"{supabase_url}/storage/v1/object/{bucket_name}/{dest_path}"

    headers = {
        "Authorization": f"Bearer {service_key}",
        "Content-Type": getattr(file_obj, "content_type", None) or "application/octet-stream",
        "x-upsert": "true",
    }

    response = requests.post(upload_url, headers=headers, data=file_obj.read())
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Supabase upload failed ({response.status_code}): {response.text}")

    # For public buckets, objects are served under /storage/v1/object/public/<bucket>/<path>
    public_url = f"{supabase_url}/storage/v1/object/public/{bucket_name}/{dest_path}"
    return public_url

def get_user_data_dict(request, user):
    data = {
        'id': user.id,
        'name': user.name,
        'username': user.username,
        'email': user.email,
        'password': user.password,  # Only include if needed for admin/debug; remove for security in production
        'address': user.address,
        'phone_no': user.phone_no,
        'phone_number': user.phone_no,
        'cnic_no': user.cnic_no,
        'cnic': user.cnic_no,
        'gender': user.gender,
        'driving_license_no': user.driving_license_no,
        'accountno': user.accountno,
        'bankname': user.bankname,
        'iban': getattr(user, 'iban', None),
        'status': user.status,
        'rejection_reason': getattr(user, 'rejection_reason', None),
        'driver_rating': user.driver_rating,
        'passenger_rating': user.passenger_rating,
        'created_at': user.created_at.isoformat() if user.created_at else None,
        'updated_at': user.updated_at.isoformat() if user.updated_at else None,
    }
    # Emergency contact, if available
    if hasattr(user, 'emergency_contact'):
        ec = user.emergency_contact
        data['emergency_contact'] = {
            'name': ec.name,
            'relation': ec.relation,
            'email': ec.email,
            'phone_no': ec.phone_no,
        }
    # Images: prefer Supabase Storage URLs if available, otherwise fall back to legacy image handlers
    image_fields = [
        'profile_photo', 'live_photo',
        'cnic_front_image', 'cnic_back_image',
        'driving_license_front', 'driving_license_back',
        'accountqr',
    ]
    for field in image_fields:
        # *_url fields added to UsersData
        url_field_name = f"{field}_url"
        storage_url = getattr(user, url_field_name, None) if hasattr(user, url_field_name) else None
        if storage_url:
            data[field] = storage_url
        elif hasattr(user, field) and getattr(user, field):
            # Fallback to binary-served endpoint if URL is not set and legacy field still exists
            data[field] = f"{url}/lets_go/user_image/{user.id}/{field}/"
        else:
            data[field] = None
    # Add vehicles if any
    vehicles = []
    if hasattr(user, 'vehicles'):
        for v in user.vehicles.all():
            photo_front_url = getattr(v, 'photo_front_url', None)
            photo_back_url = getattr(v, 'photo_back_url', None)
            documents_image_url = getattr(v, 'documents_image_url', None)

            if (
                getattr(v, 'status', None) == Vehicle.STATUS_PENDING
                and (not photo_front_url or not photo_back_url or not documents_image_url)
            ):
                cr = (
                    ChangeRequest.objects
                    .filter(
                        vehicle_id=v.id,
                        entity_type=ChangeRequest.ENTITY_VEHICLE,
                        status=ChangeRequest.STATUS_PENDING,
                    )
                    .only('requested_changes')
                    .order_by('-created_at')
                    .first()
                )
                if cr and isinstance(getattr(cr, 'requested_changes', None), dict):
                    req = cr.requested_changes
                    photo_front_url = photo_front_url or req.get('photo_front_url') or req.get('photo_front')
                    photo_back_url = photo_back_url or req.get('photo_back_url') or req.get('photo_back')
                    documents_image_url = documents_image_url or req.get('documents_image_url') or req.get('documents_image')

            vehicle_data = {
                'id': v.id,
                'model_number': v.model_number,
                'variant': v.variant,
                'company_name': v.company_name,
                'plate_number': v.plate_number,
                'vehicle_type': v.vehicle_type,
                'color': v.color,
                'seats': (v.seats if v.vehicle_type == Vehicle.FOUR_WHEELER else 2),
                'engine_number': v.engine_number,
                'chassis_number': v.chassis_number,
                'fuel_type': v.fuel_type,
                'registration_date': str(v.registration_date) if v.registration_date else None,
                'insurance_expiry': str(v.insurance_expiry) if v.insurance_expiry else None,
                'status': getattr(v, 'status', None),
                # Prefer Supabase URLs if present, otherwise fall back to legacy handlers if binary fields still exist
                'photo_front': (photo_front_url
                                or (f'{url}/lets_go/vehicle_image/{v.id}/photo_front/' if hasattr(v, 'photo_front') and v.photo_front else None)),
                'photo_back': (photo_back_url
                               or (f'{url}/lets_go/vehicle_image/{v.id}/photo_back/' if hasattr(v, 'photo_back') and v.photo_back else None)),
                'documents_image': (documents_image_url
                                    or (f'{url}/lets_go/vehicle_image/{v.id}/documents_image/' if hasattr(v, 'documents_image') and v.documents_image else None)),
            }
            vehicles.append(vehicle_data)
    data['vehicles'] = vehicles
    print(f"data: {data}")
    return data


def _parse_json_body(request):
    try:
        raw = request.body
        if not raw:
            return {}
        data = json.loads(raw.decode('utf-8')) if isinstance(raw, (bytes, bytearray)) else json.loads(raw)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def _normalize_gender(raw):
    if raw is None:
        return None
    v = str(raw).strip().lower()
    if v in ['male', 'm']:
        return 'male'
    if v in ['female', 'f']:
        return 'female'
    return None


def _get_profile_contact_change_cache_key(user_id, which, value):
    return f"profile_contact_change_{user_id}_{which}_{value}" 


def _parse_iso_date(value):
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, '%Y-%m-%d').date()
    except Exception:
        return None

def get_user_summary_dict(user):
    """Lightweight user serializer for login: avoids loading large image blobs and vehicles."""
    return {
        'id': user.id,
        'name': user.name,
        'username': user.username,
        'email': user.email,
        'address': user.address,
        'phone_no': user.phone_no,
        'cnic_no': user.cnic_no,
        'gender': user.gender,
        'status': user.status,
        'rejection_reason': getattr(user, 'rejection_reason', None),
        'driving_license_no': user.driving_license_no,
        'iban': getattr(user, 'iban', None),
        'driver_rating': user.driver_rating,
        'passenger_rating': user.passenger_rating,
        'created_at': user.created_at.isoformat() if user.created_at else None,
        'updated_at': user.updated_at.isoformat() if user.updated_at else None,
        # Do not include password, images, or vehicles here.
    }


@csrf_exempt
def reset_rejected_user(request):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=400)

    try:
        email = (request.POST.get('email') or '').strip()
        phone_no = (request.POST.get('phone_no') or '').strip()
        username = (request.POST.get('username') or '').strip()

        if not email and not phone_no and not username:
            return JsonResponse({'success': False, 'error': 'email or phone_no or username is required.'}, status=400)

        qs = UsersData.objects.all()
        if email:
            qs = qs.filter(email__iexact=email)
        if phone_no:
            qs = qs.filter(phone_no=phone_no)
        if username:
            qs = qs.filter(username__iexact=username)

        user = qs.first()
        if not user:
            return JsonResponse({'success': False, 'error': 'User not found.'}, status=404)

        if (user.status or '').upper() != 'REJECTED':
            return JsonResponse({'success': False, 'error': 'Only rejected accounts can be reset.'}, status=403)

        try:
            UsernameRegistry.objects.filter(username__iexact=user.username).delete()
        except Exception:
            pass

        user_id = user.id
        user.delete()

        try:
            request.session.flush()
        except Exception:
            pass

        return JsonResponse({'success': True, 'message': 'Rejected user reset. Please sign up again.', 'deleted_user_id': user_id})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

@csrf_exempt
def login(request):
    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        print(f"email: {email}")
        print(f"password: {password}")
        try:
            # Fetch only essential fields to avoid loading large blobs on login
            user = (
                UsersData.objects.only(
                    'id', 'name', 'username', 'email', 'password', 'address', 'phone_no',
                    'cnic_no', 'gender', 'status', 'driver_rating', 'passenger_rating',
                    'created_at', 'updated_at'
                )
                .get(email=email)
            )
            print(f" user is {user}")
            if check_password(password, user.password):
                request.session['user_id'] = user.id
                print(f"user_id: {user.id}")
                # Return a lightweight payload to keep login fast
                user_summary = get_user_summary_dict(user)
                return JsonResponse({'success': True, 'message': 'Login successful', 'UsersData': [user_summary]})
            else:
                return JsonResponse({'success': False, 'error': 'Invalid email or password'}, status=404)
        except UsersData.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Invalid email or password'}, status=404)
        except OperationalError as e:
            # Database connection/auth issue (e.g. Supabase Postgres not reachable).
            # Log and return a structured JSON error instead of an HTML 500 page.
            print('[login] OperationalError while querying UsersData:', repr(e))
            return JsonResponse({'success': False, 'error': 'Server temporarily unavailable. Please try again.'}, status=500)
        except Exception as e:
            return JsonResponse({'success': False, 'error': 'An unexpected error occurred'}, status=500)

@csrf_exempt
def logout_view(request):
    try:
        print('[logout_view] Incoming logout request')
        user_id = request.session.get('user_id')
        if not user_id and request.body:
            try:
                data = json.loads(request.body or b"{}")
                body_user_id = data.get('user_id')
                if body_user_id:
                    user_id = body_user_id
            except Exception as e:
                print('[logout_view] Failed to parse body JSON:', e)

        print(f'[logout_view] Resolved user_id={user_id}')
        if user_id:
            try:
                print(f'[logout_view] Clearing fcm_token via queryset update for user_id={user_id}')
                updated = UsersData.objects.filter(id=user_id).update(fcm_token=None)
                if updated == 0:
                    print(f'[logout_view] No UsersData row updated for id={user_id}')
                else:
                    print(f'[logout_view] Cleared fcm_token for user {user_id}')
            except OperationalError as e:
                # Database connection issue; log and continue so logout still succeeds
                print('[logout_view] OperationalError while clearing fcm_token:', repr(e))
            except Exception as e:
                print('[logout_view][ERROR during fcm_token update]:', repr(e))
        try:
            request.session.flush()
        except Exception:
            # If there is no valid session, ignore
            pass
        return JsonResponse({'success': True, 'message': 'Logout successful'})
    except Exception as e:
        import traceback
        print('[logout_view][ERROR]:', repr(e))
        traceback.print_exc()
        return JsonResponse({'success': False, 'error': str(e)}, status=500)

@csrf_exempt
def register_pending(request):
    if request.method == 'GET':
        user_id = request.session.get('user_id')
        if not user_id:
            return JsonResponse({'error': 'No user session found'}, status=400)
        user = UsersData.objects.get(id=user_id)
        print(f"user: {user}")
        user_data = get_user_data_dict(request, user)
        print(f"user_data: {user_data}")
        return JsonResponse({'message': 'Registration pending', 'UsersData': [user_data]})
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=400)


@csrf_exempt
def check_username(request):
    """Endpoint to check and reserve a username using UsernameRegistry.

    Expects POST with form fields:
      - 'username' (required): desired username
      - 'previous_username' (optional): last reserved username for this
        pending signup; if provided and different, it will be released so
        others can take it.

    Behaviour:
      - If username is already present in UsernameRegistry, returns
        {"available": false, "error": "Username already registered."}.
      - Otherwise, it either creates or updates a UsernameRegistry row to
        reserve the new username and returns {"available": true}.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request method'}, status=400)

    username = request.POST.get('username', '').strip()
    previous_username = request.POST.get('previous_username', '').strip()
    print(f"[check_username] username='{username}' previous_username='{previous_username}'")

    if not username:
        return JsonResponse({'available': False, 'error': 'Username is required.'})

    # If the client passes a previous reserved username that is changing,
    # release it so others can take it.
    if previous_username and previous_username.lower() != username.lower():
        UsernameRegistry.objects.filter(username__iexact=previous_username).delete()

    # Check if the desired username is already reserved.
    if UsernameRegistry.objects.filter(username__iexact=username).exists():
        print(f"[check_username] username '{username}' already reserved")
        return JsonResponse({'available': False, 'error': 'Username already registered.'})

    # Reserve this username (create or update for idempotency)
    obj, _ = UsernameRegistry.objects.update_or_create(
        username__iexact=username,
        defaults={'username': username},
    )
    print(f"[check_username] reserved username row: id={obj.id} username='{obj.username}'")
    return JsonResponse({'available': True})
@csrf_exempt
def signup(request):
    if request.method == 'POST':
        try:
            import json
            data = request.POST.dict()
            print(f"[signup] incoming username='{data.get('username')}' from POST")
            files = request.FILES
            email = data.get('email')
            phone = data.get('phone_no')
            if not email or not phone:
                return JsonResponse({'success': False, 'error': 'Email and phone are required.'}, status=400)
            cache_key = get_cache_key(email)
            cached = cache.get(cache_key)
            if not cached or not (cached.get('email_verified') and cached.get('phone_verified')):
                return JsonResponse({'success': False, 'error': 'Both OTPs must be verified before registration.'}, status=400)
            # Check for duplicate email/username/phone
            if UsersData.objects.filter(email=email).exists():
                return JsonResponse({'success': False, 'error': 'Email already registered.'}, status=400)
            if UsersData.objects.filter(username=data.get('username')).exists():
                return JsonResponse({'success': False, 'error': 'Username already registered.'}, status=400)
            if UsersData.objects.filter(phone_no=phone).exists():
                return JsonResponse({'success': False, 'error': 'Phone number already registered.'}, status=400)
            # Create user
            print("----------------creating user----------------")

            print(f"data: {data}")
            print(f"files: {files}")
            print(f"email: {email}")
            print(f"phone: {phone}")

            # Upload user-related images to Supabase (if provided)
            profile_photo_url = None
            live_photo_url = None
            cnic_front_url = None
            cnic_back_url = None
            dl_front_url = None
            dl_back_url = None
            accountqr_url = None

            user_bucket = getattr(settings, 'SUPABASE_USER_BUCKET', 'user-images')

            try:
                if 'profile_photo' in files:
                    dest = f"users/{email}/profile_photo.jpg"
                    profile_photo_url = upload_to_supabase(user_bucket, files['profile_photo'], dest)
                if 'live_photo' in files:
                    dest = f"users/{email}/live_photo.jpg"
                    live_photo_url = upload_to_supabase(user_bucket, files['live_photo'], dest)
                if 'cnic_front_image' in files:
                    dest = f"users/{email}/cnic_front.jpg"
                    cnic_front_url = upload_to_supabase(user_bucket, files['cnic_front_image'], dest)
                if 'cnic_back_image' in files:
                    dest = f"users/{email}/cnic_back.jpg"
                    cnic_back_url = upload_to_supabase(user_bucket, files['cnic_back_image'], dest)
                if 'driving_license_front' in files:
                    dest = f"users/{email}/driving_license_front.jpg"
                    dl_front_url = upload_to_supabase(user_bucket, files['driving_license_front'], dest)
                if 'driving_license_back' in files:
                    dest = f"users/{email}/driving_license_back.jpg"
                    dl_back_url = upload_to_supabase(user_bucket, files['driving_license_back'], dest)
                if 'accountqr' in files:
                    dest = f"users/{email}/account_qr.png"
                    accountqr_url = upload_to_supabase(user_bucket, files['accountqr'], dest)
            except Exception as upload_err:
                print(f"Supabase upload error for user images: {upload_err}")
                return JsonResponse({'success': False, 'error': 'Failed to upload images. Please try again.'}, status=500)

            user = UsersData(
                name=data.get('name', ''),
                username=data.get('username', ''),
                email=email,
                password=make_password(data.get('password', '')),
                address=data.get('address', ''),
                phone_no=phone,
                cnic_no=data.get('cnic_no', ''),
                gender=data.get('gender', ''),
                driving_license_no=data.get('driving_license_no', ''),
                accountno=data.get('accountno', ''),
                bankname=data.get('bankname', ''),
                iban=data.get('iban', ''),
                profile_photo_url=profile_photo_url,
                live_photo_url=live_photo_url,
                cnic_front_image_url=cnic_front_url,
                cnic_back_image_url=cnic_back_url,
                driving_license_front_url=dl_front_url,
                driving_license_back_url=dl_back_url,
                accountqr_url=accountqr_url,
            )
            user.save()
            # Optional emergency contact
            emergency_name = data.get('emergency_name')
            emergency_relation = data.get('emergency_relation')
            emergency_email = data.get('emergency_email')
            emergency_phone = data.get('emergency_phone_no')
            if emergency_name and emergency_relation and emergency_email and emergency_phone:
                EmergencyContact.objects.create(
                    user=user,
                    name=emergency_name,
                    relation=emergency_relation,
                    email=emergency_email,
                    phone_no=emergency_phone,
                )
            # Parse vehicles JSON
            vehicles_json = data.get('vehicles')
            if vehicles_json:
                vehicles = json.loads(vehicles_json)
                print(f"vehicles : {vehicles}")
                vehicle_bucket = getattr(settings, 'SUPABASE_VEHICLE_BUCKET', 'vehicle-images')
                for v in vehicles:
                    plate = v.get('plate_number')

                    photo_front_url = None
                    photo_back_url = None
                    documents_image_url = None

                    try:
                        front_file = files.get(f'photo_front_{plate}')
                        if front_file:
                            dest = f"vehicles/{user.id}/{plate}_front.jpg"
                            photo_front_url = upload_to_supabase(vehicle_bucket, front_file, dest)
                        back_file = files.get(f'photo_back_{plate}')
                        if back_file:
                            dest = f"vehicles/{user.id}/{plate}_back.jpg"
                            photo_back_url = upload_to_supabase(vehicle_bucket, back_file, dest)
                        docs_file = files.get(f'documents_image_{plate}')
                        if docs_file:
                            dest = f"vehicles/{user.id}/{plate}_documents.jpg"
                            documents_image_url = upload_to_supabase(vehicle_bucket, docs_file, dest)
                    except Exception as upload_err:
                        print(f"Supabase upload error for vehicle {plate}: {upload_err}")
                        return JsonResponse({'success': False, 'error': 'Failed to upload vehicle images. Please try again.'}, status=500)

                    Vehicle.objects.create(
                        owner=user,
                        model_number=v.get('model_number', ''),
                        variant=v.get('variant', ''),
                        company_name=v.get('company_name', ''),
                        plate_number=plate,
                        vehicle_type=v.get('vehicle_type', 'TW'),
                        color=v.get('color', ''),
                        photo_front_url=photo_front_url,
                        photo_back_url=photo_back_url,
                        documents_image_url=documents_image_url,
                        seats=(int(v.get('seats')) if v.get('vehicle_type') == Vehicle.FOUR_WHEELER and v.get('seats') not in [None, '', 'null'] else None),
                        engine_number=v.get('engine_number', ''),
                        chassis_number=v.get('chassis_number', ''),
                        fuel_type=v.get('fuel_type', ''),
                        registration_date=v.get('registration_date') or None,
                        insurance_expiry=v.get('insurance_expiry') or None,
                    )
            cache.delete(cache_key)
            return JsonResponse({'success': True, 'message': 'Registration successful.'})
        except Exception as e:
            print(f"error: {e}")
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
    return JsonResponse({'error': 'Invalid request method'}, status=400)

def generate_otp(length=6):
    return ''.join(random.choices(string.digits, k=length))

def get_cache_key(email):
    return f"pending_signup_{email}"

def get_reset_cache_key(method, value):
    return f"reset_pwd_{method}_{value}"

def send_otp_internal(email, phone, resend, otp_for, cached_data):
    now = int(pytime.time())
    
    # Cooldown check
    if 'email_expiry' in cached_data and now < cached_data['email_expiry'] and (resend == 'email' or resend == 'both'):
        return {'success': False, 'error': 'An OTP has already been sent. Please wait.'}
    if 'phone_expiry' in cached_data and now < cached_data['phone_expiry'] and (resend == 'phone' or resend == 'both'):
        return {'success': False, 'error': 'An OTP has already been sent. Please wait.'}

    email_otp = cached_data.get('email_otp')
    phone_otp = cached_data.get('phone_otp')

    if otp_for == 'verify_email_phoneno':
        if email and (resend in ['email', 'both']) and not cached_data.get('email_verified'):
            email_otp = generate_otp()
            print(f"email_otp for verification: {email_otp} email : {email}")
            # send_email_otp(email, email_otp)
            cached_data['email_expiry'] = now + 300
        
        if phone and (resend in ['phone', 'both']) and not cached_data.get('phone_verified'):
            phone_otp = generate_otp()
            print(f"phone_otp for verification: {phone_otp} phone: {phone}")
            # send_phone_otp(phone, phone_otp)
            cached_data['phone_expiry'] = now + 300
    else:  # for reset password
        if email and (resend in ['email', 'both']):
            email_otp = generate_otp()
            print(f"email_otp for reset password: {email_otp} email : {email}")
            # send_email_otp_for_reset(email, email_otp)
            cached_data['email_expiry'] = now + 300

        if phone and (resend in ['phone', 'both']):
            phone_otp = generate_otp()
            print(f"phone_otp for reset password: {phone_otp} phone: {phone}")
            # send_phone_otp_for_reset(phone, phone_otp)
            cached_data['phone_expiry'] = now + 300

    cached_data['email_otp'] = email_otp
    cached_data['phone_otp'] = phone_otp
    
    cache_key = get_cache_key(email if email else phone)
    
    cache.set(cache_key, cached_data, timeout=300)
    
    return {
        'success': True,
        'message': 'OTP(s) sent.',
        'email_expiry': cached_data.get('email_expiry'),
        'phone_expiry': cached_data.get('phone_expiry')
    }

@csrf_exempt
def send_otp(request):
    """
    Handles sending OTPs for both registration and password reset.
    - For registration: uses get_cache_key and stores OTPs under 'email_otp'/'phone_otp' with verification flags.
    - For reset_password: uses get_reset_cache_key and stores OTPs under 'email_otp'/'phone_otp' with verification flags.
    The frontend must send:
      - email or phone_no
      - otp_for: 'registration' or 'reset_password'
      - resend: 'email', 'phone', or 'both'
    """
    if request.method == 'POST':
        data = request.POST.dict()
        email = data.get('email', '').strip()
        phone = data.get('phone_no', '').strip()
        otp_for = data.get('otp_for', 'registration')
        resend = data.get('resend', 'both')
        print(f"data: {data}")
        print(f"email: {email}")
        print(f"phone: {phone}")
        print(f"otp_for: {otp_for}")
        print(f"resend: {resend}")
        if not email and not phone:
            return JsonResponse({'success': False, 'error': 'Email or phone is required.'}, status=400)

        # Choose the correct cache key and structure
        if otp_for == 'reset_password':
            method = 'email' if email else 'phone'
            value = email if email else phone
            cache_key = get_reset_cache_key(method, value)
        else:
            cache_key = get_cache_key(email if email else phone)

        cached = cache.get(cache_key) or {}

        # For registration, block resend if already verified
        if otp_for == 'registration' and (cached.get('email_verified') or cached.get('phone_verified')):
            return JsonResponse({'success': False, 'error': 'OTP already verified.'}, status=400)

        import random, time
        now = int(pytime.time())
        # Generate OTPs as needed
        email_otp = str(random.randint(100000, 999999)) if email else None
        phone_otp = str(random.randint(100000, 999999)) if phone else None
        email_expiry = now + 300 if email else None
        phone_expiry = now + 300 if phone else None

        # Build the cache data structure
        cache_data = {
            'email': email,
            'phone_no': phone,
            'otp_for': otp_for,
            'email_otp': email_otp if resend in ['email', 'both'] else cached.get('email_otp'),
            'phone_otp': phone_otp if resend in ['phone', 'both'] else cached.get('phone_otp'),
            'email_expiry': email_expiry if resend in ['email', 'both'] else cached.get('email_expiry'),
            'phone_expiry': phone_expiry if resend in ['phone', 'both'] else cached.get('phone_expiry'),
            'email_verified': False if resend in ['email', 'both'] else cached.get('email_verified', False),
            'phone_verified': False if resend in ['phone', 'both'] else cached.get('phone_verified', False),
        }
        cache.set(cache_key, cache_data, timeout=300)

        print(f"cache_data line 399 : {cache_data}")
        print(f"cache_key line 340 : {cache_key}")
        # if otp_for == 'registration':
        #     send_email_otp(email, cache_data['email_otp'])
        #     send_phone_otp(phone, cache_data['phone_otp'])
        # else:
        #     send_email_otp_for_reset(email, cache_data['email_otp'])
        #     send_phone_otp_for_reset(phone, cache_data['phone_otp'])


        return JsonResponse({
            'success': True,
            'message': 'OTP sent',
            'email_expiry': cache_data.get('email_expiry'),
            'phone_expiry': cache_data.get('phone_expiry')
        })
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

@csrf_exempt
def verify_otp(request):
    if request.method == 'POST':
        data = request.POST.dict()
        email = data.get('email', '').strip()
        phone = data.get('phone_no', '').strip()
        otp_for = data.get('otp_for', 'registration')
        otp = data.get('otp', '').strip()
        which = data.get('which', '')  # 'email' or 'phone'
        cache_key = get_cache_key(email if email else phone)
        cached = cache.get(cache_key)
        if not cached:
            return JsonResponse({'success': False, 'error': 'OTP session expired. Please request a new OTP.'}, status=400)
        now = int(pytime.time())
        # Check which OTP to verify
        if which == 'email' and cached.get('email_otp') == otp and now <= cached.get('email_expiry', 0):
            cached['email_verified'] = True
            cache.set(cache_key, cached, timeout=300)
            return JsonResponse({'success': True, 'message': 'Email OTP verified.'})
        elif which == 'phone' and cached.get('phone_otp') == otp and now <= cached.get('phone_expiry', 0):
            cached['phone_verified'] = True
            cache.set(cache_key, cached, timeout=300)
            return JsonResponse({'success': True, 'message': 'Phone OTP verified.'})
        else:
            return JsonResponse({'success': False, 'error': 'Invalid or expired OTP.'}, status=400)
    return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)

@csrf_exempt
def verify_password_reset_otp(request):
    if request.method == 'POST':
        method = request.POST.get('method')  # 'email' or 'phone'
        value = request.POST.get('value')    # email address or phone number
        otp = request.POST.get('otp')        # OTP entered by user
        print(f"method: {method}")
        print(f"value: {value}")
        print(f"otp: {otp}")
        # Validate required fields
        if method not in ['email', 'phone'] or not value or not otp:
            return JsonResponse({'success': False, 'error': 'Invalid data.'}, status=400)

        # Build the cache key for password reset OTPs
        cache_key = get_reset_cache_key(method, value)
        print(f"cache_key: {cache_key}")
        cached = cache.get(cache_key)
        print(f"cached: {cached}")
        if not cached:
            return JsonResponse({'success': False, 'error': 'OTP expired or not found.'}, status=400)

        # Get the correct expiry and OTP key based on method
        expiry_timestamp = cached.get('email_expiry') if method == 'email' else cached.get('phone_expiry')
        otp_key = 'email_otp' if method == 'email' else 'phone_otp'
        print(f"cached['{otp_key}']: {cached.get(otp_key)}")
        # Check if the OTP matches
        if cached.get(otp_key) == otp:
            # Mark as verified and update cache
            cache.set(cache_key, {otp_key: otp, 'verified': True, 'expiry': expiry_timestamp}, timeout=300)
            return JsonResponse({'success': True, 'message': 'OTP verified.', 'expiry': expiry_timestamp})
        else:
            return JsonResponse({'success': False, 'error': 'Invalid OTP.', 'expiry': expiry_timestamp}, status=400)
    return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=400)

@csrf_exempt
def reset_password(request):
    if request.method == 'POST':
        method = request.POST.get('method')
        value = request.POST.get('value')
        new_password = request.POST.get('new_password')
        if method not in ['email', 'phone'] or not value or not new_password:
            return JsonResponse({'success': False, 'error': 'Invalid data.'}, status=400)

        cache_key = get_reset_cache_key(method, value)
        cached = cache.get(cache_key)
        if not cached or not cached.get('verified'):
            return JsonResponse({'success': False, 'error': 'OTP not verified or expired.'}, status=400)

        try:
            if method == 'email':
                user = UsersData.objects.get(email=value)
            else:
                user = UsersData.objects.get(phone_no=value)
            user.password = make_password(new_password)
            user.save()
            cache.delete(cache_key)
            return JsonResponse({'success': True, 'message': 'Password reset successful.'})
        except UsersData.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'User not found.'}, status=404)
    return JsonResponse({'success': False, 'error': 'Invalid request method.'}, status=400)


