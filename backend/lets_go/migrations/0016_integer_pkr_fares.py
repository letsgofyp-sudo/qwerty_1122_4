from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        (
            'lets_go',
            '0015_rename_lets_go_sos_status_c_7c9c04_idx_lets_go_sos_status_438bcd_idx_and_more',
        ),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE lets_go_trip "
                        "ALTER COLUMN base_fare TYPE integer USING ROUND(base_fare)::integer;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE lets_go_trip "
                        "ALTER COLUMN minimum_acceptable_fare TYPE integer USING ROUND(minimum_acceptable_fare)::integer;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE lets_go_booking "
                        "ALTER COLUMN total_fare TYPE integer USING ROUND(total_fare)::integer;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE lets_go_booking "
                        "ALTER COLUMN original_fare TYPE integer USING ROUND(original_fare)::integer;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE lets_go_booking "
                        "ALTER COLUMN negotiated_fare TYPE integer USING ROUND(negotiated_fare)::integer;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE lets_go_booking "
                        "ALTER COLUMN passenger_offer TYPE integer USING ROUND(passenger_offer)::integer;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE lets_go_tripstopbreakdown "
                        "ALTER COLUMN price TYPE integer USING ROUND(price)::integer;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE lets_go_farematrix "
                        "ALTER COLUMN base_fare TYPE integer USING ROUND(base_fare)::integer;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE lets_go_farematrix "
                        "ALTER COLUMN peak_fare TYPE integer USING ROUND(peak_fare)::integer;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE lets_go_farematrix "
                        "ALTER COLUMN off_peak_fare TYPE integer USING ROUND(off_peak_fare)::integer;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name='trip',
                    name='base_fare',
                    field=models.IntegerField(
                        help_text='Base fare for this trip',
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                migrations.AlterField(
                    model_name='trip',
                    name='minimum_acceptable_fare',
                    field=models.IntegerField(
                        blank=True,
                        help_text='Minimum fare driver is willing to accept',
                        null=True,
                    ),
                ),
                migrations.AlterField(
                    model_name='booking',
                    name='total_fare',
                    field=models.IntegerField(
                        help_text='Total fare for all seats',
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                migrations.AlterField(
                    model_name='booking',
                    name='original_fare',
                    field=models.IntegerField(
                        blank=True,
                        help_text='Original fare before negotiation',
                        null=True,
                    ),
                ),
                migrations.AlterField(
                    model_name='booking',
                    name='negotiated_fare',
                    field=models.IntegerField(
                        blank=True,
                        help_text='Final agreed fare after negotiation',
                        null=True,
                    ),
                ),
                migrations.AlterField(
                    model_name='booking',
                    name='passenger_offer',
                    field=models.IntegerField(
                        blank=True,
                        help_text="Passenger's proposed fare",
                        null=True,
                    ),
                ),
                migrations.AlterField(
                    model_name='tripstopbreakdown',
                    name='price',
                    field=models.IntegerField(help_text='Price for this route segment'),
                ),
                migrations.AlterField(
                    model_name='farematrix',
                    name='base_fare',
                    field=models.IntegerField(
                        help_text='Standard fare for this route segment',
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                migrations.AlterField(
                    model_name='farematrix',
                    name='peak_fare',
                    field=models.IntegerField(
                        help_text='Fare during peak hours (rush hour)',
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                migrations.AlterField(
                    model_name='farematrix',
                    name='off_peak_fare',
                    field=models.IntegerField(
                        help_text='Fare during off-peak hours',
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
            ],
        ),
    ]
