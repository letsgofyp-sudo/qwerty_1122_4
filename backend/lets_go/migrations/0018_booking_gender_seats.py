from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        ('lets_go', '0017_integer_pkr_payments'),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='male_seats',
            field=models.IntegerField(default=0, help_text='Number of male seats in this booking', validators=[django.core.validators.MinValueValidator(0)]),
        ),
        migrations.AddField(
            model_name='booking',
            name='female_seats',
            field=models.IntegerField(default=0, help_text='Number of female seats in this booking', validators=[django.core.validators.MinValueValidator(0)]),
        ),
    ]
