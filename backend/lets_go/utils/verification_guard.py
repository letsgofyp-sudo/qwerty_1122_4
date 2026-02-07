from django.http import JsonResponse

from lets_go.models import UsersData, Vehicle, ChangeRequest


def verification_block_response(user_id):
    try:
        user = UsersData.objects.only('id', 'status').get(id=user_id)
    except UsersData.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'}, status=404)

    status = (getattr(user, 'status', None) or '').strip().upper()
    if status == 'BANNED':
        return JsonResponse(
            {
                'success': False,
                'error': 'Your account is banned. You cannot perform this operation.',
                'code': 'ACCOUNT_BANNED',
            },
            status=403,
        )

    # IMPORTANT: We intentionally do NOT blanket-block all operations for any pending
    # ChangeRequest or pending vehicle. Specific operations like ride creation/booking
    # should use the dedicated guards below.
    return None


def _has_any_requested_keys(change_requests, keys):
    for cr in change_requests:
        req = getattr(cr, 'requested_changes', None) or {}
        if not isinstance(req, dict):
            continue
        for k in keys:
            if k in req:
                return True
    return False


def _pending_user_profile_change_requests(user_id):
    return (
        ChangeRequest.objects
        .filter(
            user_id=user_id,
            entity_type=ChangeRequest.ENTITY_USER_PROFILE,
            status=ChangeRequest.STATUS_PENDING,
        )
        .only('id', 'requested_changes')
        .order_by('-created_at')
    )


def ride_booking_block_response(user_id):
    """Booking gate (backend):
    - Pending CNIC / gender / core profile changes => block booking.
    - Pending driving license should NOT block booking.
    """
    blocked = verification_block_response(user_id)
    if blocked is not None:
        return blocked

    try:
        pending = list(_pending_user_profile_change_requests(user_id))

        has_pending_gender = _has_any_requested_keys(pending, ['gender'])
        has_pending_user_data = _has_any_requested_keys(pending, [
            'name', 'address', 'email', 'phone_no', 'phone_number',
        ])
        has_pending_cnic = _has_any_requested_keys(pending, [
            'cnic_no', 'cnic',
            'cnic_front_image_url', 'cnic_back_image_url',
            'cnic_front_image', 'cnic_back_image',
            'cnic_front', 'cnic_back',
        ])

        if has_pending_user_data or has_pending_cnic or has_pending_gender:
            return JsonResponse(
                {
                    'success': False,
                    'error': 'Your profile verification is pending (CNIC/Gender/Profile info). Please wait for admin verification before booking rides.',
                    'code': 'VERIFICATION_PENDING',
                },
                status=403,
            )
    except Exception:
        pass

    return None


def ride_create_block_response(user_id):
    """Create ride gate (backend):
    - Pending CNIC / gender / core profile changes => block.
    - Pending driving license => block.
    - Vehicle verification is enforced separately using the selected vehicle.
    """
    blocked = verification_block_response(user_id)
    if blocked is not None:
        return blocked

    try:
        pending = list(_pending_user_profile_change_requests(user_id))

        has_pending_gender = _has_any_requested_keys(pending, ['gender'])
        has_pending_user_data = _has_any_requested_keys(pending, [
            'name', 'address', 'email', 'phone_no', 'phone_number',
        ])
        has_pending_cnic = _has_any_requested_keys(pending, [
            'cnic_no', 'cnic',
            'cnic_front_image_url', 'cnic_back_image_url',
            'cnic_front_image', 'cnic_back_image',
            'cnic_front', 'cnic_back',
        ])
        has_pending_license = _has_any_requested_keys(pending, [
            'driving_license_no',
            'driving_license_front_url', 'driving_license_back_url',
            'driving_license_front', 'driving_license_back',
        ])

        if has_pending_user_data or has_pending_cnic or has_pending_gender:
            return JsonResponse(
                {
                    'success': False,
                    'error': 'Your profile verification is pending (CNIC/Gender/Profile info). Please wait for admin verification before creating rides.',
                    'code': 'VERIFICATION_PENDING',
                },
                status=403,
            )

        if has_pending_license:
            return JsonResponse(
                {
                    'success': False,
                    'error': 'Driving license verification is pending. You can book rides, but you cannot create rides until it is verified.',
                    'code': 'DRIVING_LICENSE_PENDING',
                },
                status=403,
            )
    except Exception:
        pass

    return None
