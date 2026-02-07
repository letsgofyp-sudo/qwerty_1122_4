from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        (
            'lets_go',
            '0033_remove_supportthread_chk_supportthread_owner_xor_and_more',
        ),
    ]

    operations = [
        migrations.DeleteModel(
            name='PaymentRefund',
        ),
        migrations.DeleteModel(
            name='SeatAssignment',
        ),
        migrations.DeleteModel(
            name='FareMatrix',
        ),
    ]
