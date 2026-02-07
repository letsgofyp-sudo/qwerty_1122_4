from django.db import migrations, models
import django.core.validators


class Migration(migrations.Migration):

    dependencies = [
        (
            'lets_go',
            '0016_integer_pkr_fares',
        ),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE lets_go_trippayment "
                        "ALTER COLUMN amount TYPE integer USING ROUND(amount)::integer;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
                migrations.RunSQL(
                    sql=(
                        "ALTER TABLE lets_go_paymentrefund "
                        "ALTER COLUMN refund_amount TYPE integer USING ROUND(refund_amount)::integer;"
                    ),
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.AlterField(
                    model_name='trippayment',
                    name='amount',
                    field=models.IntegerField(
                        help_text='Payment amount',
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
                migrations.AlterField(
                    model_name='paymentrefund',
                    name='refund_amount',
                    field=models.IntegerField(
                        help_text='Amount to be refunded',
                        validators=[django.core.validators.MinValueValidator(1)],
                    ),
                ),
            ],
        ),
    ]
