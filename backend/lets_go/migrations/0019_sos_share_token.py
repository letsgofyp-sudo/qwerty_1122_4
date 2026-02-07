from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('lets_go', '0018_booking_gender_seats'),
    ]

    operations = [
        migrations.CreateModel(
            name='SosShareToken',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(db_index=True, max_length=96, unique=True)),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('expires_at', models.DateTimeField(blank=True, null=True)),
                ('revoked_at', models.DateTimeField(blank=True, null=True)),
                ('incident', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='share_tokens', to='lets_go.sosincident')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='sossharetoken',
            index=models.Index(fields=['token'], name='lets_go_sosshare_token_idx'),
        ),
        migrations.AddIndex(
            model_name='sossharetoken',
            index=models.Index(fields=['expires_at'], name='lets_go_sosshare_expires_idx'),
        ),
        migrations.AddIndex(
            model_name='sossharetoken',
            index=models.Index(fields=['revoked_at'], name='lets_go_sosshare_revoked_idx'),
        ),
    ]
