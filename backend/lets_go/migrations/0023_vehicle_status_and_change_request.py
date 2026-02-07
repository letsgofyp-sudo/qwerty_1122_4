from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('lets_go', '0022_rename_lets_go_trip_token_idx_lets_go_tri_token_fb9344_idx_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='vehicle',
            name='status',
            field=models.CharField(
                choices=[('PENDING', 'Pending'), ('VERIFIED', 'Verified'), ('REJECTED', 'Rejected')],
                default='VERIFIED',
                max_length=10,
            ),
        ),
        migrations.CreateModel(
            name='ChangeRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('entity_type', models.CharField(choices=[('USER_PROFILE', 'User Profile'), ('VEHICLE', 'Vehicle')], max_length=16)),
                ('original_data', models.JSONField(blank=True, default=dict)),
                ('requested_changes', models.JSONField(blank=True, default=dict)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('APPROVED', 'Approved'), ('REJECTED', 'Rejected')], default='PENDING', max_length=10)),
                ('review_notes', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='change_requests', to='lets_go.usersdata')),
                ('vehicle', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='change_requests', to='lets_go.vehicle')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='changerequest',
            index=models.Index(fields=['status', 'created_at'], name='lets_go_cha_status_c7ef36_idx'),
        ),
        migrations.AddIndex(
            model_name='changerequest',
            index=models.Index(fields=['entity_type', 'created_at'], name='lets_go_cha_entity_2e2d02_idx'),
        ),
        migrations.AddIndex(
            model_name='changerequest',
            index=models.Index(fields=['user', 'created_at'], name='lets_go_cha_user_i_6a0a1e_idx'),
        ),
    ]
