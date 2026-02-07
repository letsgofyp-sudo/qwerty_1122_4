# Bus/Shuttle Service Database Models

This document describes the complete database schema for the bus/shuttle booking service with group chat functionality.

## Overview

The system is designed for a bus/shuttle service where:
- **Fixed Routes**: Predefined routes with multiple stops
- **Multiple Passengers**: Each passenger can book multiple seats
- **Distance-Based Pricing**: Fare depends on pickup and drop-off stops
- **Seat Management**: Specific seat assignments with passenger visibility
- **Group Chat**: Real-time communication between all passengers and driver
- **Vehicle History**: Preserves vehicle data even when vehicles are deleted

## Model Structure

### 1. Route Management (`models_route.py`)

#### Route
- **Purpose**: Defines predefined bus/shuttle routes
- **Key Fields**: `route_id`, `route_name`, `total_distance_km`, `estimated_duration_minutes`
- **Relationships**: Has many `RouteStop`, `FareMatrix`, `Trip`

#### RouteStop
- **Purpose**: Individual stops along a route
- **Key Fields**: `stop_name`, `stop_order`, `latitude`, `longitude`, `address`
- **Relationships**: Belongs to `Route`, has many `FareMatrix` (as from_stop/to_stop)

#### FareMatrix
- **Purpose**: Defines pricing between different stops
- **Key Fields**: `from_stop`, `to_stop`, `distance_km`, `base_fare`, `peak_fare`, `off_peak_fare`
- **Relationships**: Belongs to `Route`, references `RouteStop` (from/to)

### 2. Trip Management (`models_trip.py`)

#### Trip
- **Purpose**: Individual bus/shuttle trips
- **Key Fields**: `trip_id`, `route`, `vehicle`, `driver`, `trip_date`, `departure_time`
- **Status**: SCHEDULED → IN_PROGRESS → COMPLETED/CANCELLED
- **Relationships**: Belongs to `Route`, `Vehicle`, `UsersData` (driver), has many `Booking`

#### TripVehicleHistory
- **Purpose**: Preserves vehicle data even when vehicle is deleted
- **Key Fields**: Copies all vehicle details at time of trip
- **Relationships**: One-to-one with `Trip`

### 3. Booking Management (`models_booking.py`)

#### Booking
- **Purpose**: Passenger bookings with multiple seats
- **Key Fields**: `booking_id`, `trip`, `passenger`, `from_stop`, `to_stop`, `number_of_seats`
- **Status**: CONFIRMED → COMPLETED/CANCELLED
- **Relationships**: Belongs to `Trip`, `UsersData` (passenger), `RouteStop` (from/to)

#### SeatAssignment
- **Purpose**: Detailed seat management with passenger visibility
- **Key Fields**: `seat_number`, `passenger_name`, `passenger_phone`, `is_occupied`
- **Relationships**: Belongs to `Trip`, `Booking`, `UsersData` (passenger)

### 4. Chat System (`models_chat.py`)

#### TripChatGroup
- **Purpose**: Chat groups for each trip
- **Key Fields**: `group_name`, `group_description`, `is_active`
- **Relationships**: One-to-one with `Trip`, has many `ChatGroupMember`, `ChatMessage`

#### ChatGroupMember
- **Purpose**: Members of chat groups
- **Key Fields**: `member_type` (DRIVER/PASSENGER), `notifications_enabled`, `mute_until`
- **Relationships**: Belongs to `TripChatGroup`, `UsersData`

#### ChatMessage
- **Purpose**: Individual chat messages
- **Key Fields**: `message_type`, `message_text`, `message_data`, `is_edited`, `is_deleted`
- **Types**: TEXT, IMAGE, LOCATION, SYSTEM
- **Relationships**: Belongs to `TripChatGroup`, `UsersData` (sender)

#### MessageReadStatus
- **Purpose**: Tracks which users have read messages
- **Key Fields**: `message`, `user`, `read_at`
- **Relationships**: Belongs to `ChatMessage`, `UsersData`

### 5. Payment Management (`models_payment.py`)

#### TripPayment
- **Purpose**: Individual booking payments
- **Key Fields**: `payment_method`, `amount`, `transaction_id`, `payment_status`
- **Methods**: CASH, CARD, WALLET, BANK_TRANSFER, MOBILE_MONEY
- **Relationships**: Belongs to `Booking`

#### PaymentRefund
- **Purpose**: Payment refunds
- **Key Fields**: `refund_amount`, `refund_reason`, `refund_status`
- **Relationships**: Belongs to `TripPayment`

## Key Features

### 1. Fare Calculation
- **Distance-based**: Fare calculated based on distance between stops
- **Time-based**: Different fares for peak and off-peak hours
- **Multi-seat discounts**: Discounts for booking multiple seats
- **Dynamic pricing**: Support for special pricing multipliers

### 2. Seat Management
- **Specific assignments**: Each passenger gets specific seat numbers
- **Passenger visibility**: Other passengers can see basic info (name, gender)
- **Boarding tracking**: Track when passengers board
- **Availability checking**: Real-time seat availability

### 3. Group Chat
- **Real-time communication**: All passengers and driver can chat
- **System messages**: Automatic notifications for trip events
- **Message types**: Text, images, location sharing
- **Read receipts**: Track who has read messages
- **Muting options**: Users can mute notifications

### 4. Vehicle History
- **Data preservation**: Vehicle details preserved even when deleted
- **Historical records**: Complete trip history with vehicle info
- **Audit trail**: Track all vehicle assignments

### 5. Payment Processing
- **Multiple methods**: Support for various payment methods
- **Status tracking**: Complete payment lifecycle tracking
- **Refund support**: Full refund processing
- **Gateway integration**: Support for external payment gateways

## Database Relationships

```
Route (1) ←→ (N) RouteStop
Route (1) ←→ (N) FareMatrix
Route (1) ←→ (N) Trip

Trip (1) ←→ (1) TripVehicleHistory
Trip (1) ←→ (1) TripChatGroup
Trip (1) ←→ (N) Booking
Trip (1) ←→ (N) SeatAssignment

Booking (1) ←→ (N) SeatAssignment
Booking (1) ←→ (N) TripPayment

TripChatGroup (1) ←→ (N) ChatGroupMember
TripChatGroup (1) ←→ (N) ChatMessage

ChatMessage (1) ←→ (N) MessageReadStatus

TripPayment (1) ←→ (N) PaymentRefund

UsersData (1) ←→ (N) Trip (as driver)
UsersData (1) ←→ (N) Booking (as passenger)
UsersData (1) ←→ (N) SeatAssignment (as passenger)
UsersData (1) ←→ (N) ChatGroupMember
UsersData (1) ←→ (N) ChatMessage (as sender)

Vehicle (1) ←→ (N) Trip
Vehicle (1) ←→ (1) TripVehicleHistory
```

## Usage Examples

### Creating a Route
```python
# Create a route
route = Route.objects.create(
    route_id='R001',
    route_name='Islamabad to Lahore',
    total_distance_km=350.5,
    estimated_duration_minutes=240
)

# Add stops
stop1 = RouteStop.objects.create(
    route=route,
    stop_name='Islamabad Terminal',
    stop_order=1,
    latitude=33.6844,
    longitude=73.0479
)

stop2 = RouteStop.objects.create(
    route=route,
    stop_name='Lahore Terminal',
    stop_order=2,
    latitude=31.5204,
    longitude=74.3587
)

# Add fare matrix
FareMatrix.objects.create(
    route=route,
    from_stop=stop1,
    to_stop=stop2,
    distance_km=350.5,
    base_fare=25.00,
    peak_fare=30.00,
    off_peak_fare=20.00
)
```

### Creating a Trip
```python
# Create a trip
trip = Trip.objects.create(
    trip_id='T001-2024-01-15-08:00',
    route=route,
    vehicle=vehicle,
    driver=driver,
    trip_date=date(2024, 1, 15),
    departure_time=time(8, 0),
    estimated_arrival_time=time(12, 0),
    total_seats=40,
    available_seats=40,
    base_fare=25.00
)

# Create vehicle history
vehicle_history = TripVehicleHistory.objects.create(trip=trip)
vehicle_history.copy_from_vehicle(vehicle)
```

### Making a Booking
```python
# Calculate fare
from .utils.fare_calculator import calculate_booking_fare, get_fare_matrix_for_route

fare_matrix = get_fare_matrix_for_route(route.id)
fare_breakdown = calculate_booking_fare(
    from_stop_order=1,
    to_stop_order=2,
    number_of_seats=2,
    fare_matrix=fare_matrix
)

# Create booking
booking = Booking.objects.create(
    booking_id='B001-2024-01-15-08:00-001',
    trip=trip,
    passenger=passenger,
    from_stop=stop1,
    to_stop=stop2,
    number_of_seats=2,
    total_fare=fare_breakdown['total_fare'],
    fare_breakdown=fare_breakdown
)

# Assign seats
SeatAssignment.objects.create(
    trip=trip,
    booking=booking,
    seat_number=1,
    passenger=passenger,
    passenger_name=passenger.name,
    passenger_phone=passenger.phone_no[-4:] if passenger.phone_no else None,
    passenger_gender=passenger.gender
)
```

### Chat Functionality
```python
# Get or create chat group
chat_group = trip.chat_group

# Add member
chat_group.add_member(passenger, 'PASSENGER')

# Send message
message = ChatMessage.objects.create(
    chat_group=chat_group,
    sender=passenger,
    message_type='TEXT',
    message_text='Hello everyone!'
)

# Mark as read
message.mark_as_read(passenger)

# Send system message
chat_group.send_system_message('🚌 Trip has started!')
```

## Migration Notes

1. **Run migrations**: `python manage.py makemigrations` and `python manage.py migrate`
2. **Data migration**: Existing data may need migration scripts
3. **Indexes**: All models include appropriate database indexes for performance
4. **Validation**: Comprehensive validation rules ensure data integrity

## Performance Considerations

1. **Indexes**: All foreign keys and frequently queried fields are indexed
2. **Select related**: Use `select_related()` and `prefetch_related()` for related data
3. **Bulk operations**: Use bulk create/update for large datasets
4. **Caching**: Consider caching for frequently accessed data like fare matrices

## Security Considerations

1. **Phone masking**: Only last 4 digits of phone numbers are stored in seat assignments
2. **Message deletion**: Soft delete for messages with audit trail
3. **Payment security**: External payment gateways handle sensitive data
4. **Access control**: Proper permissions for different user types 