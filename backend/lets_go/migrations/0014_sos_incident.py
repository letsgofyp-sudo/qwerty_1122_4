from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('lets_go', '0013_booking_driver_feedback_and_rating'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SosIncident',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(max_length=16)),
                ('latitude', models.DecimalField(decimal_places=8, max_digits=10)),
                ('longitude', models.DecimalField(decimal_places=8, max_digits=11)),
                ('accuracy', models.FloatField(blank=True, null=True)),
                ('note', models.TextField(blank=True, null=True)),
                ('status', models.CharField(choices=[('OPEN', 'Open'), ('RESOLVED', 'Resolved')], default='OPEN', max_length=16)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('resolved_note', models.TextField(blank=True, null=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sos_incidents', to='lets_go.usersdata')),
                ('audit_event', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sos_incidents', to='lets_go.rideauditevent')),
                ('booking', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sos_incidents', to='lets_go.booking')),
                ('resolved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='resolved_sos_incidents', to=settings.AUTH_USER_MODEL)),
                ('trip', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sos_incidents', to='lets_go.trip')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='sosincident',
            index=models.Index(fields=['status', 'created_at'], name='lets_go_sos_status_c_7c9c04_idx'),
        ),
        migrations.AddIndex(
            model_name='sosincident',
            index=models.Index(fields=['trip', 'created_at'], name='lets_go_sos_trip_crea_9e0c2f_idx'),
        ),
        migrations.AddIndex(
            model_name='sosincident',
            index=models.Index(fields=['actor', 'created_at'], name='lets_go_sos_actor_cr_981f77_idx'),
        ),
    ]
