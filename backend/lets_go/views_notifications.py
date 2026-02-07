from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import ensure_csrf_cookie
import json
import os
import threading
import requests
from .models.models_userdata import UsersData
from .constants import SUPABASE_EDGE_API_KEY
@csrf_exempt
@require_http_methods(["POST"])
def update_fcm_token(request):
    try:
        print('[update_fcm_token] Incoming request body:', request.body)
        data = json.loads(request.body or b"{}")
        print('[update_fcm_token] Decoded JSON:', data)
        user_id = data.get('user_id')
        fcm_token = data.get('fcm_token')

        print(f'[update_fcm_token] Parsed user_id={user_id} (type={type(user_id)}), '
              f'len(fcm_token)={len(fcm_token) if fcm_token else None}')

        if not user_id:
            print('[update_fcm_token] Missing user_id')
            return JsonResponse({'error': 'user_id is required'}, status=400)
        if not fcm_token:
            print('[update_fcm_token] Missing fcm_token')
            return JsonResponse({'error': 'FCM token is required'}, status=400)

        # Special case: placeholder value from client when no real device token is available
        if fcm_token == 'NO_FCM_TOKEN':
            print('[update_fcm_token] Received NO_FCM_TOKEN placeholder; skipping DB update')
            return JsonResponse({'message': 'No-op: no real FCM token provided'}, status=200)

        try:
            try:
                UsersData.objects.filter(fcm_token=fcm_token).exclude(id=user_id).update(fcm_token=None)
            except Exception as e:
                print('[update_fcm_token][WARN] Failed to clear duplicate fcm_token from other users:', repr(e))

            print(f'[update_fcm_token] Updating fcm_token via queryset for user_id={user_id}')
            updated = UsersData.objects.filter(id=user_id).update(fcm_token=fcm_token)
            if updated == 0:
                print(f'[update_fcm_token] No UsersData row updated for id={user_id}')
                return JsonResponse({'error': 'User not found'}, status=404)
            print(f'[update_fcm_token] Updated fcm_token for user {user_id}')
        except Exception as e:
            print('[update_fcm_token][ERROR during update]:', repr(e))
            return JsonResponse({'error': str(e)}, status=500)

        try:
            register_fcm_token_with_supabase_async(int(user_id), fcm_token)
        except Exception as e:
            print('[update_fcm_token][register_fcm_token_with_supabase_async][ERROR]:', repr(e))

        return JsonResponse({'message': 'FCM token updated successfully'}, status=200)

    except UsersData.DoesNotExist:
        print('[update_fcm_token][OUTER] UsersData.DoesNotExist')
        return JsonResponse({'error': 'User not found'}, status=404)
    except Exception as e:
        import traceback
        print('[update_fcm_token][OUTER][ERROR]:', repr(e))
        traceback.print_exc()
        return JsonResponse({'error': str(e)}, status=500)


SUPABASE_FN_URL = os.getenv('SUPABASE_RIDE_NOTIFICATION_URL', 'https://cjjzsswuunoquqntjctf.functions.supabase.co/send-ride-notification')
SUPABASE_REGISTER_FCM_URL = os.getenv('SUPABASE_REGISTER_FCM_URL', 'https://cjjzsswuunoquqntjctf.functions.supabase.co/register-fcm-token')
SUPABASE_FN_API_KEY = SUPABASE_EDGE_API_KEY


def _normalize_ride_notification_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {}

    recipient_id = payload.get('recipient_id')
    if recipient_id is None:
        recipient_id = payload.get('user_id')
    if recipient_id is None:
        recipient_id = payload.get('driver_id')

    sender_id = payload.get('sender_id')
    if sender_id is None:
        sender_id = payload.get('driver_id')

    def _to_str(v):
        if v is None:
            return ''
        try:
            return str(v)
        except Exception:
            return ''

    data = payload.get('data')
    if not isinstance(data, dict):
        data = {}

    # Ensure all data payload values are strings (required by FCM data payload)
    safe_data = {}
    for k, v in data.items():
        try:
            safe_key = str(k)
        except Exception:
            continue
        safe_data[safe_key] = _to_str(v)

    # Ensure data.type exists if provided at top-level (compat)
    if not safe_data.get('type'):
        if payload.get('type') is not None:
            safe_data['type'] = _to_str(payload.get('type'))

    normalized = dict(payload)
    normalized['title'] = _to_str(normalized.get('title'))
    normalized['body'] = _to_str(normalized.get('body'))
    normalized['data'] = safe_data

    normalized['recipient_id'] = _to_str(recipient_id)
    normalized['user_id'] = _to_str(recipient_id)
    normalized['sender_id'] = _to_str(sender_id)
    # Keep driver_id populated for legacy code; prefer explicit sender_id for chat.
    normalized['driver_id'] = _to_str(sender_id)

    return normalized


def send_ride_notification_async(payload: dict):
    """Fire-and-forget call to Supabase Edge Function for ride notifications.

    This must never raise back into the HTTP view; all errors are logged only.
    """

    def _worker():
        try:
            if not SUPABASE_FN_API_KEY:
                print('[send_ride_notification_async] Missing SUPABASE_EDGE_API_KEY; skipping notification')
                return
            url = SUPABASE_FN_URL
            normalized_payload = _normalize_ride_notification_payload(payload)
            print(f'[send_ride_notification_async] invoking Edge Function at {url} with payload: {normalized_payload}')
            resp = requests.post(
                url,
                headers={
                    'Content-Type': 'application/json',
                    'apikey': SUPABASE_FN_API_KEY,
                    # Supabase Edge Functions typically require an Authorization bearer token
                    'Authorization': f'Bearer {SUPABASE_FN_API_KEY}',
                },
                json=normalized_payload,
                # Slightly higher timeout to reduce spurious read timeouts in logs
                timeout=10,
            )
            print(f'[send_ride_notification_async] status={resp.status_code}, body={resp.text[:200]}')
        except Exception as e:
            print('[send_ride_notification_async][ERROR]:', e)

    threading.Thread(target=_worker, daemon=True).start()


def register_fcm_token_with_supabase_async(user_id: int, fcm_token: str):
    """Fire-and-forget call to Supabase Edge Function to register FCM token.

    This replaces the previous frontend call to `register-fcm-token`.
    """

    def _worker():
        try:
            if not SUPABASE_FN_API_KEY:
                print('[register_fcm_token_with_supabase_async] Missing SUPABASE_EDGE_API_KEY; skipping registration')
                return
            url = SUPABASE_REGISTER_FCM_URL
            payload = {
                'user_id': str(user_id),
                'fcm_token': fcm_token,
            }
            print(f'[register_fcm_token_with_supabase_async] invoking Edge Function at {url} with payload: {payload}')
            resp = requests.post(
                url,
                headers={
                    'Content-Type': 'application/json',
                    'apikey': SUPABASE_FN_API_KEY,
                    'Authorization': f'Bearer {SUPABASE_FN_API_KEY}',
                },
                json=payload,
                timeout=5,
            )
            print(f'[register_fcm_token_with_supabase_async] status={resp.status_code}, body={resp.text[:200]}')
        except Exception as e:
            print('[register_fcm_token_with_supabase_async][ERROR]:', e)

    threading.Thread(target=_worker, daemon=True).start()

