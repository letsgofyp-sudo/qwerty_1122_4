from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        (
            'lets_go',
            '0010_rename_lets_go_rid_trip_crea_5a4f79_idx_lets_go_rid_trip_id_2bdc26_idx_and_more',
        ),
    ]

    operations = [
        migrations.AddField(
            model_name='booking',
            name='seats_locked',
            field=models.BooleanField(
                default=False,
                help_text='Whether seats are currently reserved/locked for this booking request',
            ),
        ),
        migrations.AddIndex(
            model_name='booking',
            index=models.Index(fields=['seats_locked'], name='letsgo_book_seats_l_idx'),
        ),
    ]
