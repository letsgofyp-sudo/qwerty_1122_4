from . import views
from django.urls import path

app_name = 'administration'

urlpatterns = [
    # Admin view
    path('', views.admin_view, name='admin_view'),
    path('analytics/', views.analytics_view, name='analytics_view'),
    path('settings/', views.settings_view, name='settings_view'),
    path('change-requests/', views.change_requests_list_view, name='change_requests_list'),
    path('change-requests/<int:change_request_id>/', views.change_request_detail_view, name='change_request_detail'),
    path('rides/', views.rides_dashboard_view, name='rides_dashboard'),
    path('rides/trip/<int:trip_pk>/', views.admin_trip_detail_view, name='admin_trip_detail'),
    path('rides/booking/<int:booking_pk>/map/', views.admin_booking_map_view, name='admin_booking_map'),
    path('sos/', views.sos_dashboard_view, name='sos_dashboard'),
    path('sos/<int:incident_id>/', views.sos_incident_detail_view, name='sos_incident_detail'),
    path('sos/<int:incident_id>/resolve/', views.sos_incident_resolve_view, name='sos_incident_resolve'),
    path("api/kpis/", views.api_kpis, name="api_kpis"),
    path("api/chart-data/", views.api_chart_data, name="api_chart_data"),

    # Login / Logout
    path('login/', views.login_view, name='login_view'),
    path('logout/', views.logout_view, name='logout_view'),

    # Users list + API
    path('users/', views.user_list_view, name='user_list'),
    path('users/api/', views.api_users, name='api_users'),

    # Guest support chat
    path('guests/', views.guest_list_view, name='guest_list'),
    path('guests/api/', views.api_guests, name='api_guests'),
    path('guests/<int:guest_id>/support-chat/', views.guest_support_chat_view, name='guest_support_chat'),

    # User add
    path('users/add/', views.user_add_view, name='user_add'),

    # User detail
    path('users/<int:user_id>/view/', views.user_detail_view, name='user_detail'),
    path('users/<int:user_id>/support-chat/', views.user_support_chat_view, name='user_support_chat'),
    path('users/<int:user_id>/view/api/', views.api_user_detail, name='api_user_detail'),
    path('users/<int:user_id>/view/status/', views.update_user_status_view, name='update_user_status'),

    # User edit
    path('users/<int:user_id>/edit/', views.user_edit_view, name='user_edit'),
    path('users/<int:user_id>/edit/submit/', views.submit_user_edit, name='submit_user_edit'),

    # User vehicles CRUD (scoped under user)
    path('users/<int:user_id>/vehicles/', views.vehicle_detail_view, name='vehicle_detail'),
    path('users/<int:user_id>/vehicles/api/', views.api_user_vehicles, name='api_user_vehicles'),
    path('users/<int:user_id>/vehicles/add/', views.vehicle_add_view, name='vehicle_add'),
    path('users/<int:user_id>/vehicles/<int:vehicle_id>/edit/', views.vehicle_edit_view, name='vehicle_edit'),
    path('users/<int:user_id>/vehicles/<int:vehicle_id>/status/', views.vehicle_update_status_view, name='vehicle_update_status'),
    path('users/<int:user_id>/vehicles/<int:vehicle_id>/delete/', views.vehicle_delete_view, name='vehicle_delete'),
]
