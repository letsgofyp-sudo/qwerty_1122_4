from django.http import JsonResponse, HttpResponse, Http404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.utils import OperationalError
from django.conf import settings
import time as pytime

from .models import UsersData, Vehicle, Trip, EmergencyContact, ChangeRequest
from .constants import url
from .email_otp import send_email_otp
from .phone_otp_send import send_phone_otp
from .views_authentication import (
    upload_to_supabase,
    get_user_data_dict,
    _parse_json_body,
    _normalize_gender,
    _get_profile_contact_change_cache_key,
    _parse_iso_date,
    generate_otp,
)


@csrf_exempt
def user_change_requests(request, user_id):
    if request.method != 'GET':
        return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=400)
    try:
        UsersData.objects.only('id').get(id=user_id)

        entity_type = (request.GET.get('entity_type') or '').strip().upper()
        status = (request.GET.get('status') or '').strip().upper()
        vehicle_id_raw = (request.GET.get('vehicle_id') or '').strip()
        limit_raw = (request.GET.get('limit') or '').strip()

        try:
            limit = int(limit_raw) if limit_raw else 20
        except Exception:
            limit = 20
        if limit < 1:
            limit = 1
        if limit > 50:
            limit = 50

        qs = (
            ChangeRequest.objects
            .filter(user_id=user_id)
            .select_related('vehicle')
            .only(
                'id', 'entity_type', 'status', 'review_notes',
                'original_data', 'requested_changes',
                'created_at', 'reviewed_at',
                'vehicle__id', 'vehicle__plate_number',
            )
            .order_by('-created_at')
        )

        if entity_type in [ChangeRequest.ENTITY_USER_PROFILE, ChangeRequest.ENTITY_VEHICLE]:
            qs = qs.filter(entity_type=entity_type)
        if status in [ChangeRequest.STATUS_PENDING, ChangeRequest.STATUS_APPROVED, ChangeRequest.STATUS_REJECTED]:
            qs = qs.filter(status=status)

        if vehicle_id_raw:
            try:
                vehicle_id = int(vehicle_id_raw)
                qs = qs.filter(vehicle_id=vehicle_id)
            except Exception:
                pass

        out = []
        for cr in qs[:limit]:
            out.append({
                'id': cr.id,
                'entity_type': cr.entity_type,
                'status': cr.status,
                'review_notes': cr.review_notes,
                'created_at': cr.created_at.isoformat() if cr.created_at else None,
                'reviewed_at': cr.reviewed_at.isoformat() if cr.reviewed_at else None,
                'original_data': cr.original_data or {},
                'requested_changes': cr.requested_changes or {},
                'vehicle_id': cr.vehicle_id,
                'vehicle_plate_number': getattr(getattr(cr, 'vehicle', None), 'plate_number', None),
            })

        return JsonResponse({'success': True, 'change_requests': out})
    except UsersData.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def send_profile_contact_change_otp(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)
    try:
        user = UsersData.objects.only('id', 'email', 'phone_no').get(id=user_id)
        data = request.POST.dict()
        if not data:
            data = _parse_json_body(request)

        which = (data.get('which') or '').strip().lower()
        value = (data.get('value') or '').strip()
        resend = (data.get('resend') or 'false').strip().lower() in ['1', 'true', 'yes']

        if which not in ['email', 'phone']:
            return JsonResponse({'success': False, 'error': 'Invalid which'}, status=400)
        if not value:
            return JsonResponse({'success': False, 'error': 'Value is required'}, status=400)

        if which == 'phone' and not value.startswith('+'):
            value = '+' + value

        cache_key = _get_profile_contact_change_cache_key(user.id, which, value)
        cached = cache.get(cache_key) or {}
        now = int(pytime.time())

        if cached and not resend:
            expiry = int(cached.get('expiry') or 0)
            if now <= expiry:
                return JsonResponse({'success': False, 'error': 'An OTP has already been sent. Please wait.'}, status=400)

        otp = generate_otp()
        expiry = now + 300
        cache_data = {
            'user_id': user.id,
            'which': which,
            'value': value,
            'otp': otp,
            'expiry': expiry,
            'verified': False,
        }
        cache.set(cache_key, cache_data, timeout=300)

        ok = True
        if which == 'email':
            ok = send_email_otp(value, otp)
        else:
            ok = send_phone_otp(value, otp)

        if not ok:
            return JsonResponse({'success': False, 'error': 'Failed to send OTP. Please try again.'}, status=500)

        return JsonResponse({'success': True, 'message': 'OTP sent', 'expiry': expiry})
    except UsersData.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def upload_user_driving_license(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=400)
    try:
        user = UsersData.objects.get(id=user_id)

        if (
            ChangeRequest.objects
            .filter(user_id=user.id, entity_type=ChangeRequest.ENTITY_USER_PROFILE, status=ChangeRequest.STATUS_PENDING)
            .only('id')
            .exists()
        ):
            return JsonResponse(
                {
                    'success': False,
                    'error': 'You already have a pending verification request. Please wait for admin review before updating sensitive documents again.',
                    'code': 'CHANGE_REQUEST_PENDING',
                },
                status=403,
            )
        old_license_no = getattr(user, 'driving_license_no', None)
        old_front_url = getattr(user, 'driving_license_front_url', None)
        old_back_url = getattr(user, 'driving_license_back_url', None)

        front = request.FILES.get('front') or request.FILES.get('driving_license_front')
        back = request.FILES.get('back') or request.FILES.get('driving_license_back')
        license_no = (request.POST.get('driving_license_no') or request.POST.get('license_no') or '').strip()

        if not front and not back and not license_no:
            return JsonResponse({'success': False, 'error': 'Nothing to update.'}, status=400)

        user_bucket = getattr(settings, 'SUPABASE_USER_BUCKET', 'user-images')
        stamp = int(pytime.time())

        requested = {}

        if front:
            ext = (getattr(front, 'name', '') or 'jpg').rsplit('.', 1)[-1].lower()
            dest = f"users/{user.email}/driving_license_front_{stamp}.{ext}"
            requested['driving_license_front_url'] = upload_to_supabase(user_bucket, front, dest)
        if back:
            ext = (getattr(back, 'name', '') or 'jpg').rsplit('.', 1)[-1].lower()
            dest = f"users/{user.email}/driving_license_back_{stamp}.{ext}"
            requested['driving_license_back_url'] = upload_to_supabase(user_bucket, back, dest)
        if license_no:
            requested['driving_license_no'] = license_no
        if requested:
            rejected = (
                ChangeRequest.objects
                .filter(
                    user_id=user.id,
                    entity_type=ChangeRequest.ENTITY_USER_PROFILE,
                    status=ChangeRequest.STATUS_REJECTED,
                )
                .order_by('-created_at')
                .first()
            )

            if rejected and isinstance(getattr(rejected, 'requested_changes', None), dict):
                keys = [str(k) for k in (rejected.requested_changes or {}).keys()]
                is_license_rejected = any(k.startswith('driving_license_') for k in keys)
            else:
                is_license_rejected = False

            original_data = {
                'driving_license_no': old_license_no,
                'driving_license_front_url': old_front_url,
                'driving_license_back_url': old_back_url,
            }

            if rejected and is_license_rejected:
                rejected.original_data = original_data
                rejected.requested_changes = requested
                rejected.status = ChangeRequest.STATUS_PENDING
                rejected.review_notes = None
                rejected.reviewed_at = None
                rejected.save(update_fields=['original_data', 'requested_changes', 'status', 'review_notes', 'reviewed_at'])
            else:
                ChangeRequest.objects.create(
                    user=user,
                    entity_type=ChangeRequest.ENTITY_USER_PROFILE,
                    original_data=original_data,
                    requested_changes=requested,
                    status=ChangeRequest.STATUS_PENDING,
                )

        return JsonResponse({
            'success': True,
            'message': 'Driving license update request submitted for verification.',
            'user': get_user_data_dict(request, user),
        })
    except UsersData.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)
    except ValidationError as ve:
        return JsonResponse({'success': False, 'error': str(ve)}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def upload_user_photos(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=400)
    try:
        user = UsersData.objects.get(id=user_id)

        profile_photo = request.FILES.get('profile_photo') or request.FILES.get('profile')
        live_photo = request.FILES.get('live_photo') or request.FILES.get('live')

        if not profile_photo and not live_photo:
            return JsonResponse({'success': False, 'error': 'At least one image file is required.'}, status=400)

        user_bucket = getattr(settings, 'SUPABASE_USER_BUCKET', 'user-images')
        stamp = int(pytime.time())

        if profile_photo:
            ext = (getattr(profile_photo, 'name', '') or 'jpg').rsplit('.', 1)[-1].lower()
            dest = f"users/{user.email}/profile_photo_{stamp}.{ext}"
            user.profile_photo_url = upload_to_supabase(user_bucket, profile_photo, dest)

        if live_photo:
            ext = (getattr(live_photo, 'name', '') or 'jpg').rsplit('.', 1)[-1].lower()
            dest = f"users/{user.email}/live_photo_{stamp}.{ext}"
            user.live_photo_url = upload_to_supabase(user_bucket, live_photo, dest)

        user.full_clean()
        user.save()

        return JsonResponse({
            'success': True,
            'message': 'Photos updated.',
            'user': get_user_data_dict(request, user),
        })
    except UsersData.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)
    except ValidationError as ve:
        return JsonResponse({'success': False, 'error': str(ve)}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def upload_user_cnic(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=400)
    try:
        user = UsersData.objects.get(id=user_id)

        if (
            ChangeRequest.objects
            .filter(user_id=user.id, entity_type=ChangeRequest.ENTITY_USER_PROFILE, status=ChangeRequest.STATUS_PENDING)
            .only('id')
            .exists()
        ):
            return JsonResponse(
                {
                    'success': False,
                    'error': 'You already have a pending verification request. Please wait for admin review before updating sensitive documents again.',
                    'code': 'CHANGE_REQUEST_PENDING',
                },
                status=403,
            )
        old_cnic_no = getattr(user, 'cnic_no', None)
        old_front_url = getattr(user, 'cnic_front_image_url', None)
        old_back_url = getattr(user, 'cnic_back_image_url', None)

        front = request.FILES.get('front') or request.FILES.get('cnic_front') or request.FILES.get('cnic_front_image')
        back = request.FILES.get('back') or request.FILES.get('cnic_back') or request.FILES.get('cnic_back_image')
        cnic_no = (request.POST.get('cnic_no') or request.POST.get('cnic') or '').strip()

        if not front and not back and not cnic_no:
            return JsonResponse({'success': False, 'error': 'Nothing to update.'}, status=400)

        user_bucket = getattr(settings, 'SUPABASE_USER_BUCKET', 'user-images')
        stamp = int(pytime.time())

        requested = {}

        if front:
            ext = (getattr(front, 'name', '') or 'jpg').rsplit('.', 1)[-1].lower()
            dest = f"users/{user.email}/cnic_front_{stamp}.{ext}"
            requested['cnic_front_image_url'] = upload_to_supabase(user_bucket, front, dest)
        if back:
            ext = (getattr(back, 'name', '') or 'jpg').rsplit('.', 1)[-1].lower()
            dest = f"users/{user.email}/cnic_back_{stamp}.{ext}"
            requested['cnic_back_image_url'] = upload_to_supabase(user_bucket, back, dest)
        if cnic_no:
            requested['cnic_no'] = cnic_no
        if requested:
            rejected = (
                ChangeRequest.objects
                .filter(
                    user_id=user.id,
                    entity_type=ChangeRequest.ENTITY_USER_PROFILE,
                    status=ChangeRequest.STATUS_REJECTED,
                )
                .order_by('-created_at')
                .first()
            )

            if rejected and isinstance(getattr(rejected, 'requested_changes', None), dict):
                keys = [str(k) for k in (rejected.requested_changes or {}).keys()]
                is_cnic_rejected = any(k.startswith('cnic_') for k in keys) or any('cnic_front' in k or 'cnic_back' in k for k in keys)
            else:
                is_cnic_rejected = False

            original_data = {
                'cnic_no': old_cnic_no,
                'cnic_front_image_url': old_front_url,
                'cnic_back_image_url': old_back_url,
            }

            if rejected and is_cnic_rejected:
                rejected.original_data = original_data
                rejected.requested_changes = requested
                rejected.status = ChangeRequest.STATUS_PENDING
                rejected.review_notes = None
                rejected.reviewed_at = None
                rejected.save(update_fields=['original_data', 'requested_changes', 'status', 'review_notes', 'reviewed_at'])
            else:
                ChangeRequest.objects.create(
                    user=user,
                    entity_type=ChangeRequest.ENTITY_USER_PROFILE,
                    original_data=original_data,
                    requested_changes=requested,
                    status=ChangeRequest.STATUS_PENDING,
                )

        return JsonResponse({
            'success': True,
            'message': 'CNIC update request submitted for verification.',
            'user': get_user_data_dict(request, user),
        })
    except UsersData.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)
    except ValidationError as ve:
        return JsonResponse({'success': False, 'error': str(ve)}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def upload_vehicle_images(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=400)
    try:
        UsersData.objects.only('id').get(id=user_id)

        plate = (request.POST.get('plate_number') or request.POST.get('plate') or '').strip()
        if not plate:
            return JsonResponse({'success': False, 'error': 'plate_number is required.'}, status=400)

        front = request.FILES.get('photo_front')
        back = request.FILES.get('photo_back')
        docs = request.FILES.get('documents_image')

        if not front and not back and not docs:
            return JsonResponse({'success': False, 'error': 'At least one image file is required.'}, status=400)

        vehicle_bucket = getattr(settings, 'SUPABASE_VEHICLE_BUCKET', 'vehicle-images')
        stamp = int(pytime.time())

        out = {
            'photo_front_url': None,
            'photo_back_url': None,
            'documents_image_url': None,
        }

        if front:
            ext = (getattr(front, 'name', '') or 'jpg').rsplit('.', 1)[-1].lower()
            dest = f"vehicles/{user_id}/{plate}/front_{stamp}.{ext}"
            out['photo_front_url'] = upload_to_supabase(vehicle_bucket, front, dest)
        if back:
            ext = (getattr(back, 'name', '') or 'jpg').rsplit('.', 1)[-1].lower()
            dest = f"vehicles/{user_id}/{plate}/back_{stamp}.{ext}"
            out['photo_back_url'] = upload_to_supabase(vehicle_bucket, back, dest)
        if docs:
            ext = (getattr(docs, 'name', '') or 'jpg').rsplit('.', 1)[-1].lower()
            dest = f"vehicles/{user_id}/{plate}/documents_{stamp}.{ext}"
            out['documents_image_url'] = upload_to_supabase(vehicle_bucket, docs, dest)

        return JsonResponse({'success': True, **out})
    except UsersData.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def verify_profile_contact_change_otp(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request'}, status=400)
    try:
        user = UsersData.objects.get(id=user_id)
        data = request.POST.dict()
        if not data:
            data = _parse_json_body(request)

        which = (data.get('which') or '').strip().lower()
        value = (data.get('value') or '').strip()
        otp = (data.get('otp') or '').strip()

        if which not in ['email', 'phone']:
            return JsonResponse({'success': False, 'error': 'Invalid which'}, status=400)
        if not value or not otp:
            return JsonResponse({'success': False, 'error': 'Value and OTP are required'}, status=400)
        if which == 'phone' and not value.startswith('+'):
            value = '+' + value

        cache_key = _get_profile_contact_change_cache_key(user.id, which, value)
        cached = cache.get(cache_key)
        if not cached:
            return JsonResponse({'success': False, 'error': 'OTP session expired. Please request a new OTP.'}, status=400)

        now = int(pytime.time())
        expiry = int(cached.get('expiry') or 0)
        if now > expiry:
            cache.delete(cache_key)
            return JsonResponse({'success': False, 'error': 'Invalid or expired OTP.'}, status=400)

        if cached.get('otp') != otp:
            return JsonResponse({'success': False, 'error': 'Invalid or expired OTP.'}, status=400)

        if which == 'email':
            user.email = value
        else:
            user.phone_no = value

        try:
            user.full_clean()
            user.save()
        except IntegrityError:
            return JsonResponse({'success': False, 'error': f'{which.title()} already in use.'}, status=400)
        except ValidationError as ve:
            return JsonResponse({'success': False, 'error': str(ve)}, status=400)

        cache.delete(cache_key)

        return JsonResponse({
            'success': True,
            'message': f'{which.title()} updated successfully.',
            'user': get_user_data_dict(request, user),
        })
    except UsersData.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def user_profile(request, user_id):
    """Return a user's profile with license-related fields so the app can detect driver status.
    Returns a lightweight object with URLs for images (no binary blobs).
    """
    try:
        if request.method == 'GET':
            user = (
                UsersData.objects.only(
                    'id', 'name', 'username', 'email', 'address', 'phone_no',
                    'cnic_no', 'gender', 'status', 'driver_rating', 'passenger_rating',
                    'created_at', 'updated_at',
                    'driving_license_no',
                    'accountno', 'bankname', 'iban',
                ).get(id=user_id)
            )
            data = get_user_data_dict(request, user)
            return JsonResponse(data)

        if request.method not in ['PUT', 'PATCH']:
            return JsonResponse({'error': 'Invalid request method'}, status=400)

        user = UsersData.objects.get(id=user_id)
        payload = _parse_json_body(request)

        has_pending_profile_cr = (
            ChangeRequest.objects
            .filter(user_id=user.id, entity_type=ChangeRequest.ENTITY_USER_PROFILE, status=ChangeRequest.STATUS_PENDING)
            .only('id')
            .exists()
        )

        immediate_updates = {}
        pending_updates = {}

        for k in ['name', 'address', 'accountno', 'bankname', 'iban', 'accountqr_url']:
            if k in payload:
                immediate_updates[k] = payload.get(k)

        if 'gender' in payload:
            if has_pending_profile_cr:
                return JsonResponse(
                    {
                        'success': False,
                        'error': 'You already have a pending verification request for profile changes. Please wait for admin review.',
                        'code': 'CHANGE_REQUEST_PENDING',
                    },
                    status=403,
                )
            normalized = _normalize_gender(payload.get('gender'))
            if normalized is None:
                return JsonResponse({'error': 'Invalid gender'}, status=400)
            if normalized != user.gender:
                pending_updates['gender'] = normalized

        if immediate_updates:
            for k, v in immediate_updates.items():
                setattr(user, k, v)
            user.full_clean()
            user.save()

        change_request_id = None
        if pending_updates:
            original = {k: getattr(user, k, None) for k in pending_updates.keys()}
            cr = ChangeRequest.objects.create(
                user=user,
                entity_type=ChangeRequest.ENTITY_USER_PROFILE,
                original_data=original,
                requested_changes=pending_updates,
                status=ChangeRequest.STATUS_PENDING,
            )
            change_request_id = cr.id

        refreshed = get_user_data_dict(request, user)
        return JsonResponse({
            'user': refreshed,
            'immediate_updates': immediate_updates,
            'pending_updates': pending_updates,
            'change_request_id': change_request_id,
        })
    except UsersData.DoesNotExist:
        return JsonResponse({'error': 'User not found'}, status=404)
    except OperationalError as e:
        # Mirror logout_view behaviour: log DB issues but do not crash the app.
        print('[user_profile] OperationalError while loading user data:', repr(e))
        return JsonResponse({'error': 'temporary database issue'}, status=200)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
def upload_user_accountqr(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=400)
    try:
        user = UsersData.objects.get(id=user_id)

        file_obj = request.FILES.get('file') or request.FILES.get('accountqr')
        if not file_obj:
            return JsonResponse({'success': False, 'error': 'Image file is required.'}, status=400)

        user_bucket = getattr(settings, 'SUPABASE_USER_BUCKET', 'user-images')
        ext = 'png'
        try:
            name = getattr(file_obj, 'name', '') or ''
            if '.' in name:
                ext = name.rsplit('.', 1)[-1].lower() or 'png'
        except Exception:
            ext = 'png'

        stamp = int(pytime.time())
        dest = f"users/{user.email}/account_qr_{stamp}.{ext}"
        accountqr_url = upload_to_supabase(user_bucket, file_obj, dest)

        user.accountqr_url = accountqr_url
        user.full_clean()
        user.save()

        return JsonResponse({
            'success': True,
            'message': 'Account QR uploaded.',
            'user': get_user_data_dict(request, user),
            'accountqr_url': accountqr_url,
        })
    except UsersData.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def user_emergency_contact(request, user_id):
    try:
        user = UsersData.objects.get(id=user_id)

        if request.method == 'GET':
            ec = EmergencyContact.objects.filter(user=user).first()
            if not ec:
                return JsonResponse({'success': True, 'emergency_contact': None})
            return JsonResponse({
                'success': True,
                'emergency_contact': {
                    'name': ec.name,
                    'relation': ec.relation,
                    'email': ec.email,
                    'phone_no': ec.phone_no,
                }
            })

        if request.method not in ['PUT', 'PATCH', 'POST']:
            return JsonResponse({'success': False, 'error': 'Invalid request method'}, status=400)

        payload = _parse_json_body(request)
        if not payload:
            payload = request.POST.dict()

        name = (payload.get('name') or '').strip()
        relation = (payload.get('relation') or '').strip()
        email = (payload.get('email') or '').strip()
        phone_no = (payload.get('phone_no') or '').strip()
        if not name or not relation or not email or not phone_no:
            return JsonResponse({'success': False, 'error': 'All fields are required.'}, status=400)

        ec, _ = EmergencyContact.objects.update_or_create(
            user=user,
            defaults={
                'name': name,
                'relation': relation,
                'email': email,
                'phone_no': phone_no,
            }
        )
        ec.full_clean()
        ec.save()

        refreshed = get_user_data_dict(request, user)
        return JsonResponse({
            'success': True,
            'message': 'Emergency contact updated.',
            'user': refreshed,
            'emergency_contact': {
                'name': ec.name,
                'relation': ec.relation,
                'email': ec.email,
                'phone_no': ec.phone_no,
            },
        })
    except UsersData.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)
    except ValidationError as ve:
        return JsonResponse({'success': False, 'error': str(ve)}, status=400)
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def user_image(request, user_id, image_field):
    """Serve user profile images (legacy binary fields).

    Note: For newer deployments we primarily use Supabase URLs stored in
    *_url fields. This view is now a best-effort fallback for old records
    which still have binary image columns.
    """
    try:
        print(f"Attempting to serve {image_field} for user {user_id}")
        # Allow only known legacy image field names that actually exist
        legacy_fields = {
            'profile_photo',
            'live_photo',
            'cnic_front_image',
            'cnic_back_image',
            'driving_license_front',
            'driving_license_back',
            'accountqr',
        }

        if image_field not in legacy_fields or not hasattr(UsersData, image_field):
            print(f"Invalid image field requested: {image_field}")
            raise Http404("Invalid image field")

        # Increase statement timeout locally for this request to avoid large-blob timeouts
        from django.db import connection
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET LOCAL statement_timeout = 30000")  # 30 seconds
        except Exception as e:
            print(f"Warning: could not set local statement_timeout: {e}")

        # Fetch only the specific binary field to avoid loading entire row with large blobs
        image_data = (
            UsersData.objects.only(image_field)
            .values_list(image_field, flat=True)
            .get(id=user_id)
        )
        
        print(f"Image data type: {type(image_data)}")
        try:
            print(f"Image data length: {len(image_data) if image_data is not None else 'None'}")
        except Exception:
            pass
        
        if not image_data:
            print(f"No image data found for {image_field}")
            raise Http404("Image not found")
        
        # Handle different image data types
        if isinstance(image_data, bytes):
            # already bytes
            pass
        elif isinstance(image_data, memoryview):
            # Convert memoryview to bytes
            image_data = image_data.tobytes()
        elif isinstance(image_data, str):
            # Data might be base64 encoded or a file path
            try:
                # Try to decode base64 if it's encoded
                import base64
                image_data = base64.b64decode(image_data)
            except:
                # If not base64, treat as file path
                raise Http404("Invalid image format")
        else:
            # Convert to bytes if possible
            try:
                image_data = bytes(image_data)
            except:
                raise Http404("Invalid image format")
        
        # Determine content type based on image field
        content_type = 'image/jpeg'  # Default
        if image_field in ['profile_photo', 'live_photo']:
            content_type = 'image/jpeg'
        elif image_field in ['cnic_front_image', 'cnic_back_image']:
            content_type = 'image/jpeg'
        elif image_field == 'accountqr':
            content_type = 'image/png'  # QR codes are usually PNG
        
        print(f"Serving image with content type: {content_type}")
        try:
            print(f"Final image data length: {len(image_data)} bytes")
        except Exception:
            pass
        
        # Set cache headers for better performance
        response = HttpResponse(image_data, content_type=content_type)
        response['Cache-Control'] = 'public, max-age=3600'  # Cache for 1 hour
        return response
        
    except UsersData.DoesNotExist:
        print(f"User {user_id} not found")
        raise Http404("User not found")
    except Exception as e:
        print(f"Error serving image {image_field} for user {user_id}: {str(e)}")
        raise Http404("Image not found")


@require_GET
def vehicle_image(request, vehicle_id, image_field):
    """Serve vehicle images"""
    try:
        print(f"Attempting to serve {image_field} for vehicle {vehicle_id}")
        vehicle = Vehicle.objects.get(id=vehicle_id)
        image_data = getattr(vehicle, image_field)
        
        print(f"Image data type: {type(image_data)}")
        print(f"Image data length: {len(image_data) if image_data else 'None'}")
        
        if not image_data:
            print(f"No image data found for {image_field}")
            raise Http404("Image not found")
        
        # Handle different image data types
        if isinstance(image_data, bytes):
            # Data is already in bytes format
            pass
        elif isinstance(image_data, str):
            # Data might be base64 encoded or a file path
            try:
                # Try to decode base64 if it's encoded
                import base64
                image_data = base64.b64decode(image_data)
            except:
                # If not base64, treat as file path
                raise Http404("Invalid image format")
        else:
            # Convert to bytes if possible
            try:
                image_data = bytes(image_data)
            except:
                raise Http404("Invalid image format")
        
        # Determine content type based on image field
        content_type = 'image/jpeg'  # Default for vehicle photos
        
        print(f"Serving image with content type: {content_type}")
        print(f"Final image data length: {len(image_data)} bytes")
        
        # Set cache headers for better performance
        response = HttpResponse(image_data, content_type=content_type)
        response['Cache-Control'] = 'public, max-age=3600'  # Cache for 1 hour
        return response
        
    except Vehicle.DoesNotExist:
        print(f"Vehicle {vehicle_id} not found")
        raise Http404("Vehicle not found")
    except Exception as e:
        print(f"Error serving image {image_field} for vehicle {vehicle_id}: {str(e)}")
        raise Http404("Image not found")


@csrf_exempt
def user_vehicles(request, user_id):
    try:
        user = UsersData.objects.only('id').get(id=user_id)

        if request.method == 'POST':
            payload = request.POST.dict()
            if not payload:
                payload = _parse_json_body(request)

            vehicle_type = payload.get('vehicle_type') or Vehicle.TWO_WHEELER
            raw_seats = payload.get('seats')
            seats_value = None
            if vehicle_type == Vehicle.FOUR_WHEELER:
                if raw_seats not in [None, '', 'null']:
                    try:
                        seats_value = int(raw_seats)
                    except Exception:
                        seats_value = None
            else:
                # Two wheelers should not set seats (model validation enforces this)
                seats_value = None

            vehicle = Vehicle(
                owner=user,
                model_number=payload.get('model_number') or payload.get('model') or '',
                variant=payload.get('variant') or '',
                company_name=payload.get('company_name') or payload.get('make') or '',
                plate_number=payload.get('plate_number') or payload.get('registration_no') or payload.get('registration_number') or '',
                vehicle_type=vehicle_type,
                color=payload.get('color') or '',
                seats=seats_value,
                engine_number=payload.get('engine_number') or '',
                chassis_number=payload.get('chassis_number') or '',
                fuel_type=payload.get('fuel_type') or '',
                registration_date=_parse_iso_date(payload.get('registration_date')),
                insurance_expiry=_parse_iso_date(payload.get('insurance_expiry')),
                photo_front_url=payload.get('photo_front_url') or payload.get('photo_front'),
                photo_back_url=payload.get('photo_back_url') or payload.get('photo_back'),
                documents_image_url=payload.get('documents_image_url') or payload.get('documents_image'),
                status=Vehicle.STATUS_PENDING,
            )

            try:
                vehicle.full_clean()
                vehicle.save()
            except Exception as e:
                return JsonResponse({'success': False, 'error': str(e)}, status=400)

            requested = {
                'model_number': vehicle.model_number,
                'variant': vehicle.variant,
                'company_name': vehicle.company_name,
                'plate_number': vehicle.plate_number,
                'vehicle_type': vehicle.vehicle_type,
                'color': vehicle.color,
                'seats': vehicle.seats,
                'engine_number': vehicle.engine_number,
                'chassis_number': vehicle.chassis_number,
                'fuel_type': vehicle.fuel_type,
                'registration_date': (vehicle.registration_date.isoformat() if vehicle.registration_date else None),
                'insurance_expiry': (vehicle.insurance_expiry.isoformat() if vehicle.insurance_expiry else None),
                'photo_front_url': vehicle.photo_front_url,
                'photo_back_url': vehicle.photo_back_url,
                'documents_image_url': vehicle.documents_image_url,
            }
            cr = ChangeRequest.objects.create(
                user=user,
                vehicle=vehicle,
                entity_type=ChangeRequest.ENTITY_VEHICLE,
                original_data={},
                requested_changes=requested,
                status=ChangeRequest.STATUS_PENDING,
            )

            return JsonResponse({
                'success': True,
                'vehicle_id': vehicle.id,
                'vehicle_status': vehicle.status,
                'change_request_id': cr.id,
            })

        if request.method != 'GET':
            return JsonResponse({'error': 'Invalid request method'}, status=400)

        # Important: Build a lightweight vehicle list to avoid loading binary image fields.
        # Do not access v.photo_* attributes directly, as that may load large blobs.
        qs = (
            Vehicle.objects
            .filter(owner_id=user_id)
            .only(
                'id', 'model_number', 'company_name', 'plate_number',
                'vehicle_type', 'color', 'seats', 'fuel_type', 'variant',
                'engine_number', 'chassis_number', 'registration_date', 'insurance_expiry', 'status'
            )
            .defer(
                'photo_front', 'photo_back', 'documents_image'
            )
        )

        vehicles = []
        for v in qs:
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

            vehicles.append({
                'id': v.id,
                # New, minimal keys
                'model': v.model_number,
                'make': v.company_name,
                'registration_no': v.plate_number,
                'vehicle_type': v.vehicle_type,
                'color': v.color,
                'seats': (v.seats if v.vehicle_type == Vehicle.FOUR_WHEELER else 2),
                'fuel_type': (v.get_fuel_type_display() if hasattr(v, 'get_fuel_type_display') and v.fuel_type else ''),
                'variant': v.variant,
                'engine_number': v.engine_number,
                'chassis_number': v.chassis_number,
                'registration_date': (v.registration_date.isoformat() if v.registration_date else None),
                'insurance_expiry': (v.insurance_expiry.isoformat() if v.insurance_expiry else None),
                'status': getattr(v, 'status', None),
                # Compatibility keys expected by existing Flutter UI
                'model_number': v.model_number,
                'company_name': v.company_name,
                'plate_number': v.plate_number,
                # Prefer Supabase Storage URLs for images; legacy /vehicle_image endpoints are deprecated
                'photo_front': photo_front_url,
                'photo_back': photo_back_url,
                'documents_image': documents_image_url,
            })

        return JsonResponse({'vehicles': vehicles})
    except UsersData.DoesNotExist:
        return JsonResponse({'vehicles': []})


@csrf_exempt
def vehicle_detail(request, vehicle_id):
    try:
        if request.method == 'DELETE':
            active = Trip.objects.filter(vehicle_id=vehicle_id, trip_status__in=['SCHEDULED', 'IN_PROGRESS']).exists()
            if active:
                return JsonResponse({
                    'success': False,
                    'error': 'This vehicle is used in an active ride. Please cancel/delete that trip first to edit/delete this vehicle.'
                }, status=400)
            Vehicle.objects.filter(id=vehicle_id).delete()
            return JsonResponse({'success': True})

        if request.method in ['PATCH', 'PUT']:
            active = Trip.objects.filter(vehicle_id=vehicle_id, trip_status__in=['SCHEDULED', 'IN_PROGRESS']).exists()
            if active:
                return JsonResponse({
                    'success': False,
                    'error': 'This vehicle is used in an active ride. Please cancel/delete that trip first to edit/delete this vehicle.'
                }, status=400)

            v = Vehicle.objects.select_related('owner').get(id=vehicle_id)
            payload = _parse_json_body(request)

            if (
                ChangeRequest.objects
                .filter(vehicle_id=v.id, entity_type=ChangeRequest.ENTITY_VEHICLE, status=ChangeRequest.STATUS_PENDING)
                .only('id')
                .exists()
            ):
                return JsonResponse(
                    {
                        'success': False,
                        'error': 'You already have a pending verification request for this vehicle. Please wait for admin review before changing vehicle details again.',
                        'code': 'CHANGE_REQUEST_PENDING',
                    },
                    status=403,
                )

            immediate_updates = {}
            requested_changes = {}

            if 'registration_date' in payload:
                immediate_updates['registration_date'] = _parse_iso_date(payload.get('registration_date'))
            if 'insurance_expiry' in payload:
                immediate_updates['insurance_expiry'] = _parse_iso_date(payload.get('insurance_expiry'))

            for k in [
                'model_number', 'company_name', 'plate_number', 'vehicle_type',
                'color', 'seats', 'fuel_type', 'variant',
                'engine_number', 'chassis_number',
                'photo_front_url', 'photo_back_url', 'documents_image_url',
            ]:
                if k in payload:
                    requested_changes[k] = payload.get(k)

            if immediate_updates:
                for k, v2 in immediate_updates.items():
                    setattr(v, k, v2)
                try:
                    v.full_clean()
                    v.save()
                except Exception as e:
                    return JsonResponse({'success': False, 'error': str(e)}, status=400)

            change_request_id = None
            if requested_changes:
                if getattr(v, 'status', None) == Vehicle.STATUS_PENDING:
                    original = {k: getattr(v, k, None) for k in requested_changes.keys()}
                    cr = ChangeRequest.objects.create(
                        user=v.owner,
                        vehicle=v,
                        entity_type=ChangeRequest.ENTITY_VEHICLE,
                        original_data=original,
                        requested_changes=requested_changes,
                        status=ChangeRequest.STATUS_PENDING,
                    )
                    change_request_id = cr.id

                    for k, v2 in requested_changes.items():
                        setattr(v, k, v2)
                    try:
                        v.full_clean()
                        v.save()
                    except Exception as e:
                        return JsonResponse({'success': False, 'error': str(e)}, status=400)
                else:
                    original = {k: getattr(v, k, None) for k in requested_changes.keys()}
                    cr = ChangeRequest.objects.create(
                        user=v.owner,
                        vehicle=v,
                        entity_type=ChangeRequest.ENTITY_VEHICLE,
                        original_data=original,
                        requested_changes=requested_changes,
                        status=ChangeRequest.STATUS_PENDING,
                    )
                    change_request_id = cr.id
                    if getattr(v, 'status', None) != Vehicle.STATUS_PENDING:
                        v.status = Vehicle.STATUS_PENDING
                        v.save(update_fields=['status'])

            data = {
                'success': True,
                'vehicle_id': v.id,
                'vehicle_status': getattr(v, 'status', None),
                'immediate_updates': {
                    'registration_date': (v.registration_date.isoformat() if v.registration_date else None),
                    'insurance_expiry': (v.insurance_expiry.isoformat() if v.insurance_expiry else None),
                },
                'pending_updates': requested_changes,
                'change_request_id': change_request_id,
            }
            return JsonResponse(data)

        if request.method != 'GET':
            return JsonResponse({'error': 'Invalid request method'}, status=400)

        v = (
            Vehicle.objects
            .only(
                'id', 'model_number', 'company_name', 'plate_number',
                'vehicle_type', 'color', 'seats', 'fuel_type', 'variant',
                'engine_number', 'chassis_number', 'registration_date', 'insurance_expiry', 'status'
            )
            .get(id=vehicle_id)
        )

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

        data = {
            'id': v.id,
            'model_number': v.model_number,
            'company_name': v.company_name,
            'plate_number': v.plate_number,
            'vehicle_type': v.vehicle_type,
            'color': v.color,
            'seats': (v.seats if v.vehicle_type == Vehicle.FOUR_WHEELER else 2),
            'fuel_type': (v.get_fuel_type_display() if hasattr(v, 'get_fuel_type_display') and v.fuel_type else ''),
            'variant': v.variant,
            'engine_number': v.engine_number,
            'chassis_number': v.chassis_number,
            'registration_date': (v.registration_date.isoformat() if v.registration_date else None),
            'insurance_expiry': (v.insurance_expiry.isoformat() if v.insurance_expiry else None),
            'status': getattr(v, 'status', None),
            # Prefer Supabase URLs if present, otherwise fall back to binary handlers if legacy fields still exist
            'photo_front': (photo_front_url
                            or (f'{url}/lets_go/vehicle_image/{v.id}/photo_front/' if hasattr(v, 'photo_front') and v.photo_front else None)),
            'photo_back': (photo_back_url
                           or (f'{url}/lets_go/vehicle_image/{v.id}/photo_back/' if hasattr(v, 'photo_back') and v.photo_back else None)),
            'documents_image': (documents_image_url
                                or (f'{url}/lets_go/vehicle_image/{v.id}/documents_image/' if hasattr(v, 'documents_image') and v.documents_image else None)),
        }
        return JsonResponse(data)
    except Vehicle.DoesNotExist:
        return JsonResponse({'error': 'Vehicle not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
