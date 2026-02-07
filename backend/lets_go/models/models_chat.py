from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone


class TripChatGroup(models.Model):
    """Model for trip chat groups"""
    trip = models.OneToOneField('Trip', on_delete=models.CASCADE, related_name='chat_group')
    group_name = models.CharField(
        max_length=100,
        help_text="Name of the chat group"
    )
    group_description = models.TextField(
        null=True, 
        blank=True,
        help_text="Description of the chat group"
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Whether this chat group is active"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        'UsersData', 
        on_delete=models.CASCADE, 
        related_name='created_chat_groups',
        help_text="Driver who created the chat group"
    )
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['trip']),
            models.Index(fields=['is_active']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"Chat Group: {self.group_name} - Trip {self.trip.trip_id}"
    
    @property
    def members(self):
        """Get all active members"""
        return self.chat_members.filter(is_active=True)
    
    @property
    def messages(self):
        """Get all non-deleted messages"""
        return self.chat_messages.filter(is_deleted=False).order_by('created_at')
    
    @property
    def member_count(self):
        """Get number of active members"""
        return self.members.count()
    
    def add_member(self, user, member_type='PASSENGER'):
        """Add a member to the chat group"""
        member, created = ChatGroupMember.objects.get_or_create(
            chat_group=self,
            user=user,
            defaults={'member_type': member_type}
        )
        return member
    
    def remove_member(self, user):
        """Remove a member from the chat group"""
        try:
            member = self.chat_members.get(user=user)
            member.leave_group()
        except ChatGroupMember.DoesNotExist:
            pass
    
    def send_system_message(self, message_text):
        """Send a system message"""
        return ChatMessage.objects.create(
            chat_group=self,
            sender=self.created_by,
            message_type='SYSTEM',
            message_text=message_text,
            message_data={'is_system': True}
        )
    
    def archive(self):
        """Archive the chat group"""
        self.is_active = False
        self.archived_at = timezone.now()
        self.save()
    
    def get_unread_count(self, user):
        """Get number of unread messages for a user"""
        return self.messages.exclude(
            sender=user
        ).exclude(
            message_read_status__user=user
        ).count()

class ChatGroupMember(models.Model):
    """Model for chat group members"""
    MEMBER_TYPE_CHOICES = [
        ('DRIVER', 'Driver'),
        ('PASSENGER', 'Passenger'),
    ]
    
    chat_group = models.ForeignKey(TripChatGroup, on_delete=models.CASCADE, related_name='chat_members')
    user = models.ForeignKey('UsersData', on_delete=models.CASCADE, related_name='chat_group_memberships')
    member_type = models.CharField(
        max_length=20, 
        choices=MEMBER_TYPE_CHOICES,
        help_text="Type of member (driver or passenger)"
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, help_text="Whether member is currently active")
    last_read_message = models.ForeignKey(
        'ChatMessage', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        help_text="Last message read by this member"
    )
    
    # Member preferences
    notifications_enabled = models.BooleanField(
        default=True,
        help_text="Whether to receive notifications"
    )
    mute_until = models.DateTimeField(
        null=True, 
        blank=True,
        help_text="Mute notifications until this time"
    )

    class Meta:
        unique_together = ['chat_group', 'user']
        indexes = [
            models.Index(fields=['chat_group']),
            models.Index(fields=['user']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return f"{self.user.name} ({self.member_type}) in {self.chat_group.group_name}"
    
    def leave_group(self):
        """Leave the chat group"""
        self.is_active = False
        self.left_at = timezone.now()
        self.save()
    
    def update_last_read(self, message):
        """Update last read message"""
        self.last_read_message = message
        self.save()
    
    def is_muted(self):
        """Check if member has muted notifications"""
        if not self.notifications_enabled:
            return True
        if self.mute_until and timezone.now() < self.mute_until:
            return True
        return False

class ChatMessage(models.Model):
    """Model for chat messages"""
    MESSAGE_TYPE_CHOICES = [
        ('TEXT', 'Text'),
        ('IMAGE', 'Image'),
        ('LOCATION', 'Location'),
        ('SYSTEM', 'System'),
    ]
    
    chat_group = models.ForeignKey(TripChatGroup, on_delete=models.CASCADE, related_name='chat_messages')
    sender = models.ForeignKey('UsersData', on_delete=models.CASCADE, related_name='sent_messages')
    message_type = models.CharField(
        max_length=20, 
        choices=MESSAGE_TYPE_CHOICES, 
        default='TEXT',
        help_text="Type of message"
    )
    message_text = models.TextField(help_text="Message content")
    message_data = models.JSONField(
        default=dict, 
        blank=True,
        help_text="Additional data for images, locations, etc."
    )
    
    # Message status
    is_edited = models.BooleanField(default=False, help_text="Whether message has been edited")
    edited_at = models.DateTimeField(null=True, blank=True)
    is_deleted = models.BooleanField(default=False, help_text="Whether message has been deleted")
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        'UsersData', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name='deleted_messages',
        help_text="User who deleted the message"
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['chat_group']),
            models.Index(fields=['sender']),
            models.Index(fields=['created_at']),
            models.Index(fields=['message_type']),
            models.Index(fields=['is_deleted']),
        ]
        ordering = ['created_at']

    def __str__(self):
        return f"Message from {self.sender.name} in {self.chat_group.group_name}"
    
    @property
    def read_by(self):
        """Get users who have read this message"""
        return self.message_read_status.all()
    
    @property
    def unread_by(self):
        """Get users who haven't read this message"""
        group_members = self.chat_group.members.exclude(user=self.sender)
        read_users = set(self.read_by.values_list('user_id', flat=True))
        return group_members.exclude(user_id__in=read_users)
    
    @property
    def is_system_message(self):
        """Check if this is a system message"""
        return self.message_type == 'SYSTEM'
    
    def mark_as_read(self, user):
        """Mark message as read by a user"""
        MessageReadStatus.objects.get_or_create(
            message=self,
            user=user,
            defaults={'read_at': timezone.now()}
        )
    
    def edit_message(self, new_text, edited_by):
        """Edit the message"""
        if self.is_deleted:
            raise ValidationError('Cannot edit a deleted message.')
        
        if self.sender != edited_by:
            raise ValidationError('Only the sender can edit the message.')
        
        self.message_text = new_text
        self.is_edited = True
        self.edited_at = timezone.now()
        self.save()
    
    def delete_message(self, deleted_by_user):
        """Delete the message"""
        if self.is_deleted:
            return  # Already deleted
        
        # Only sender or admin can delete
        if self.sender != deleted_by_user:
            # Check if user is admin (you can implement admin check here)
            pass
        
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.deleted_by = deleted_by_user
        self.save()
    
    def get_display_text(self):
        """Get display text for the message"""
        if self.is_deleted:
            return "[Message deleted]"
        
        if self.message_type == 'SYSTEM':
            return self.message_text
        
        if self.is_edited:
            return f"{self.message_text} (edited)"
        
        return self.message_text
    
    def get_message_preview(self, max_length=50):
        """Get a preview of the message"""
        text = self.get_display_text()
        if len(text) <= max_length:
            return text
        return text[:max_length] + "..."
    
    def get_attachment_info(self):
        """Get information about message attachments"""
        if self.message_type == 'IMAGE':
            return {
                'type': 'image',
                'url': self.message_data.get('image_url'),
                'caption': self.message_data.get('caption', '')
            }
        elif self.message_type == 'LOCATION':
            return {
                'type': 'location',
                'latitude': self.message_data.get('latitude'),
                'longitude': self.message_data.get('longitude'),
                'location_name': self.message_data.get('location_name', '')
            }
        return None

class MessageReadStatus(models.Model):
    """Model to track message read status"""
    message = models.ForeignKey(ChatMessage, on_delete=models.CASCADE, related_name='message_read_status')
    user = models.ForeignKey('UsersData', on_delete=models.CASCADE, related_name='read_messages')
    read_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['message', 'user']
        indexes = [
            models.Index(fields=['message']),
            models.Index(fields=['user']),
            models.Index(fields=['read_at']),
        ]

    def __str__(self):
        return f"{self.user.name} read message at {self.read_at}"


class OfflineMessageQueue(models.Model):
    """Unmanaged model mapping to Supabase offline_message_queue table"""
    id = models.BigAutoField(primary_key=True)
    is_delivered = models.BooleanField()
    created_at = models.DateTimeField()
    delivered_at = models.DateTimeField(null=True, blank=True)
    chat_room_id = models.BigIntegerField()
    message_id = models.BigIntegerField()
    user = models.ForeignKey('UsersData', on_delete=models.CASCADE)

    class Meta:
        db_table = 'offline_message_queue'
        managed = False