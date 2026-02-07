from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('lets_go', '0020_rename_lets_go_sosshare_token_idx_lets_go_sos_token_b71096_idx_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='TripShareToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('role', models.CharField(max_length=16)),
                ('token', models.CharField(db_index=True, max_length=96, unique=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('booking', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='share_tokens', to='lets_go.booking')),
                ('trip', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='share_tokens', to='lets_go.trip')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='tripsharetoken',
            index=models.Index(fields=['token'], name='lets_go_trip_token_idx'),
        ),
        migrations.AddIndex(
            model_name='tripsharetoken',
            index=models.Index(fields=['expires_at'], name='lets_go_trip_expires_idx'),
        ),
        migrations.AddIndex(
            model_name='tripsharetoken',
            index=models.Index(fields=['revoked_at'], name='lets_go_trip_revoked_idx'),
        ),
    ]
