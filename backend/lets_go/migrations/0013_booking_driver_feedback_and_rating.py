from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('lets_go', '0012_rename_letsgo_book_seats_l_idx_lets_go_boo_seats_l_eb6fea_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='driver_feedback',
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='booking',
            name='driver_rating',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=3, null=True, validators=[django.core.validators.MinValueValidator(1.0), django.core.validators.MaxValueValidator(5.0)]),
        ),
    ]
