from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('lets_go', '0026_usersdata_rejection_reason'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='blocked',
            field=models.BooleanField(default=False, help_text='If true, passenger is blocked from requesting this trip again'),
        ),
        migrations.CreateModel(
            name='BlockedUser',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('blocked_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='blocked_by_users', to='lets_go.usersdata')),
                ('blocker', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='blocked_users', to='lets_go.usersdata')),
            ],
            options={
                'unique_together': {('blocker', 'blocked_user')},
            },
        ),
        migrations.AddIndex(
            model_name='blockeduser',
            index=models.Index(fields=['blocker'], name='lets_go_blo_blocker_4f5a1b_idx'),
        ),
        migrations.AddIndex(
            model_name='blockeduser',
            index=models.Index(fields=['blocked_user'], name='lets_go_blo_blocked__9d0f07_idx'),
        ),
    ]
