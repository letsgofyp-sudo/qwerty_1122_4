from django.urls import path
from . import views_authentication
from . import views_profile
from . import views_homescreen
from . import views_rideposting
from . import views_ridebooking
from . import views_negotiation
from . import views_blocking
from . import views_chat
from . import views_notifications
from . import views_post_booking
from . import views_incidents
from . import views_support_chat

urlpatterns = [
    path('login/', views_authentication.login, name='login'),
    path('register_pending/', views_authentication.register_pending, name='register_pending'),
    path('signup/', views_authentication.signup, name='signup'),
    path('reset_rejected_user/', views_authentication.reset_rejected_user, name='reset_rejected_user'),
    path('check_username/', views_authentication.check_username, name='check_username'),
    path('send_otp/', views_authentication.send_otp, name='send_otp'),
    path('verify_otp/', views_authentication.verify_otp, name='verify_otp'),
    path('verify_password_reset_otp/', views_authentication.verify_password_reset_otp, name='verify_password_reset_otp'),
    path('reset_password/', views_authentication.reset_password, name='reset_password'),
    # User profile (lightweight) for Flutter role detection
    path('users/<int:user_id>/', views_profile.user_profile, name='user_profile'),
    path('users/<int:user_id>/contact-change/send-otp/', views_profile.send_profile_contact_change_otp, name='send_profile_contact_change_otp'),
    path('users/<int:user_id>/contact-change/verify-otp/', views_profile.verify_profile_contact_change_otp, name='verify_profile_contact_change_otp'),
    path('users/<int:user_id>/accountqr/upload/', views_profile.upload_user_accountqr, name='upload_user_accountqr'),
    path('users/<int:user_id>/driving-license/upload/', views_profile.upload_user_driving_license, name='upload_user_driving_license'),
    path('users/<int:user_id>/cnic/upload/', views_profile.upload_user_cnic, name='upload_user_cnic'),
    path('users/<int:user_id>/photos/upload/', views_profile.upload_user_photos, name='upload_user_photos'),
    path('users/<int:user_id>/vehicle-images/upload/', views_profile.upload_vehicle_images, name='upload_vehicle_images'),
    path('users/<int:user_id>/change-requests/', views_profile.user_change_requests, name='user_change_requests'),
    path('users/<int:user_id>/emergency-contact/', views_profile.user_emergency_contact, name='user_emergency_contact'),
    path('create_trip/', views_rideposting.create_trip, name='create_trip'),
    path('all_trips/', views_homescreen.all_trips, name='all_trips'),
    path('trip/<str:trip_id>/breakdown/', views_rideposting.get_trip_breakdown, name='get_trip_breakdown'),
    path('users/<int:user_id>/vehicles/', views_profile.user_vehicles, name='user_vehicles'),
    path('vehicles/<int:vehicle_id>/', views_profile.vehicle_detail, name='vehicle_detail'),
    path('create_route/', views_rideposting.create_route, name='create_route'),
    # path('calculate_fare/', views_rideposting.calculate_fare, name='calculate_fare'),
    
    # Image serving endpoints
    path('user_image/<int:user_id>/<str:image_field>/', views_profile.user_image, name='user_image'),
    path('vehicle_image/<int:vehicle_id>/<str:image_field>/', views_profile.vehicle_image, name='vehicle_image'),
    
    # MyRides API endpoints
    path('users/<int:user_id>/rides/', views_rideposting.get_user_rides, name='get_user_rides'),
    path('stops/suggest/', views_homescreen.suggest_stops, name='suggest_stops'),
    path('trips/search/', views_homescreen.search_trips, name='search_trips'),
    # Support chat (Bot + Admin)
    path('support/guest/', views_support_chat.support_guest, name='support_guest'),
    path('support/bot/', views_support_chat.view_bot, name='view_bot'),
    path('support/admin/', views_support_chat.view_adminchat, name='view_adminchat'),
    path('trips/<str:trip_id>/', views_rideposting.get_trip_details, name='get_trip_details'),
    path('trips/<str:trip_id>/update/', views_rideposting.update_trip, name='update_trip'),
    path('trips/<str:trip_id>/delete/', views_rideposting.delete_trip, name='delete_trip'),
    path('trips/<str:trip_id>/cancel/', views_rideposting.cancel_trip, name='cancel_trip'),
    
    # Ride Booking endpoints
    path('ride-booking/<str:trip_id>/', views_ridebooking.get_ride_booking_details, name='get_ride_booking_details'),
    path('ride-booking/<str:trip_id>/request/', views_negotiation.handle_ride_booking_request, name='handle_ride_booking_request'),
    path('ride-booking/<str:trip_id>/passengers/', views_ridebooking.get_confirmed_passengers, name='get_confirmed_passengers'),
    # Driver request management (negotiation)
    path('ride-booking/<str:trip_id>/requests/', views_negotiation.list_pending_requests, name='list_pending_requests'),
    path('ride-booking/<str:trip_id>/requests/<int:booking_id>/', views_negotiation.booking_request_details, name='booking_request_details'),
    path('ride-booking/<str:trip_id>/requests/<int:booking_id>/respond/', views_negotiation.respond_booking_request, name='respond_booking_request'),
    # Passenger decision endpoint (negotiation)
    path('ride-booking/<str:trip_id>/requests/<int:booking_id>/passenger-respond/', views_negotiation.passenger_respond_booking, name='passenger_respond_booking'),
    # Negotiation history endpoint
    path('ride-booking/<str:trip_id>/negotiation/<int:booking_id>/', views_negotiation.get_booking_negotiation_history, name='get_booking_negotiation_history'),

    # Blocking / Blocklist
    path('users/<int:user_id>/blocked/', views_blocking.list_blocked_users, name='list_blocked_users'),
    path('users/<int:user_id>/blocked/<int:blocked_user_id>/unblock/', views_blocking.unblock_user, name='unblock_user'),
    path('ride-booking/<str:trip_id>/blocked/<int:passenger_id>/unblock/', views_negotiation.unblock_passenger_for_trip, name='unblock_passenger_for_trip'),
    
    # Additional endpoints that might be needed
    path('routes/<int:route_id>/', views_rideposting.get_route_details, name='get_route_details'),
    path('routes/<int:route_id>/statistics/', views_rideposting.get_route_statistics, name='get_route_statistics'),
    path('routes/search/', views_rideposting.search_routes, name='search_routes'),
    path('trips/<int:trip_id>/available-seats/', views_rideposting.get_available_seats, name='get_available_seats'),
    path('bookings/', views_rideposting.create_booking, name='create_booking'),
    path('bookings/<int:booking_id>/cancel/', views_rideposting.cancel_booking, name='cancel_booking'),
    path('users/<int:user_id>/bookings/', views_rideposting.get_user_bookings, name='get_user_bookings'),
    path('rides/search/', views_rideposting.search_rides, name='search_rides'),
    path('rides/<int:ride_id>/', views_rideposting.cancel_ride, name='cancel_ride'),
    # Post-booking: live tracking and start ride
    path('trips/<str:trip_id>/start-ride/', views_post_booking.start_trip_ride, name='start_trip_ride'),
    path('trips/<str:trip_id>/complete-ride/', views_post_booking.complete_trip_ride, name='complete_trip_ride'),
    path('bookings/<int:booking_id>/start-ride/', views_post_booking.start_booking_ride, name='start_booking_ride'),
    path('bookings/<int:booking_id>/dropped-off/', views_post_booking.mark_booking_dropped_off, name='mark_booking_dropped_off'),
    path('bookings/<int:booking_id>/driver-reached-pickup/', views_post_booking.driver_mark_reached_pickup, name='driver_mark_reached_pickup'),
    path('bookings/<int:booking_id>/driver-reached-dropoff/', views_post_booking.driver_mark_reached_dropoff, name='driver_mark_reached_dropoff'),
    path('trips/<str:trip_id>/location/update/', views_post_booking.update_live_location, name='update_live_location'),
    path('trips/<str:trip_id>/location/', views_post_booking.get_live_location, name='get_live_location'),
    path('trips/<str:trip_id>/bookings/<int:booking_id>/pickup-code/', views_post_booking.generate_pickup_code, name='generate_pickup_code'),
    path('pickup-code/verify/', views_post_booking.verify_pickup_code, name='verify_pickup_code'),
    path('bookings/<int:booking_id>/payment/', views_post_booking.get_booking_payment_details, name='get_booking_payment_details'),
    path('bookings/<int:booking_id>/payment/submit/', views_post_booking.submit_booking_payment, name='submit_booking_payment'),
    path('bookings/<int:booking_id>/payment/confirm/', views_post_booking.confirm_booking_payment, name='confirm_booking_payment'),
    path('trips/<str:trip_id>/payments/', views_post_booking.get_trip_payments, name='get_trip_payments'),
    # Chat endpoints
    path('chat/<str:trip_id>/messages/', views_chat.list_chat_messages, name='list_chat_messages'),
    path('chat/<str:trip_id>/messages/updates/', views_chat.list_chat_messages_updates, name='list_chat_messages_updates'),
    path('chat/<str:trip_id>/messages/send/', views_chat.send_chat_message, name='send_chat_message'),
    path('chat/<str:trip_id>/messages/broadcast/', views_chat.send_broadcast_message, name='send_broadcast_message'),
    path('chat/messages/<int:message_id>/read/', views_chat.mark_message_read, name='mark_message_read'),
    # Notification endpoints
    path('update_fcm_token/', views_notifications.update_fcm_token, name='update_fcm_token'),
    # Public share links (non-SOS)
    path('trips/<str:trip_id>/share/', views_incidents.trip_share_token, name='trip_share_token'),
    path('trips/share/<str:token>/', views_incidents.trip_share_view, name='trip_share'),
    path('trips/share/<str:token>/live/', views_incidents.trip_share_live, name='trip_share_live'),
    path('incidents/sos/', views_incidents.sos_incident, name='sos_incident'),
    path('incidents/sos/share/<str:token>/', views_incidents.sos_share_view, name='sos_share'),
    path('incidents/sos/share/<str:token>/send', views_incidents.sos_share_send, name='sos_share_send'),
    path('incidents/sos/share/<str:token>/live/', views_incidents.sos_share_live, name='sos_share_live'),
    path('logout/', views_authentication.logout_view, name='logout'),
]