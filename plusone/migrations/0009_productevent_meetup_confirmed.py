from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("plusone", "0008_productevent_opener_clicked"),
    ]

    operations = [
        migrations.AlterField(
            model_name="productevent",
            name="name",
            field=models.CharField(
                choices=[
                    ("publish_card", "Card published"),
                    ("match_created", "Match created"),
                    ("opener_suggested", "Openers suggested"),
                    ("opener_clicked", "Opener suggestion clicked"),
                    ("first_message_sent", "First message sent"),
                    ("message_sent", "Message sent"),
                    ("first_reply_received", "First reply received"),
                    ("agree_clicked", "Agree clicked"),
                    ("meetup_confirmed", "Meetup outcome confirmed"),
                ],
                max_length=40,
            ),
        ),
    ]
