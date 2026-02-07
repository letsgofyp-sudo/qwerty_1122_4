import json

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import UsersData, BlockedUser


def _user_brief(u: UsersData):
    return {
        'id': u.id,
        'name': u.name,
        'username': u.username,
        'profile_photo_url': getattr(u, 'profile_photo_url', None),
    }


@csrf_exempt
def list_blocked_users(request, user_id: int):
    if request.method != 'GET':
        return JsonResponse({'success': False, 'error': 'Only GET allowed'}, status=405)

    try:
        qs = (
            BlockedUser.objects
            .select_related('blocked_user')
            .filter(blocker_id=user_id)
            .order_by('-created_at')
        )
        items = []
        for r in qs:
            bu = r.blocked_user
            if not bu:
                continue
            items.append({
                'blocked_user': _user_brief(bu),
                'reason': r.reason,
                'created_at': r.created_at.isoformat() if r.created_at else None,
            })
        return JsonResponse({'success': True, 'blocked': items})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@csrf_exempt
def unblock_user(request, user_id: int, blocked_user_id: int):
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'Only POST allowed'}, status=405)

    try:
        BlockedUser.objects.filter(blocker_id=user_id, blocked_user_id=blocked_user_id).delete()
        return JsonResponse({'success': True, 'message': 'User unblocked'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=500)
