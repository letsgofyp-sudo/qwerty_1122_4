from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('lets_go', '0035_alter_booking_ride_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='pre_ride_reminder_sent',
            field=models.BooleanField(
                default=False,
                help_text='Has the T-10 pre-ride reminder been sent for this booking?',
            ),
        ),
    ]
