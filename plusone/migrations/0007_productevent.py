import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("plusone", "0006_llmlog_opening_assistant_choice"),
    ]

    operations = [
        migrations.CreateModel(
            name="ProductEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "name",
                    models.CharField(
                        choices=[
                            ("publish_card", "Card published"),
                            ("match_created", "Match created"),
                            ("opener_suggested", "Openers suggested"),
                            ("first_message_sent", "First message sent"),
                            ("message_sent", "Message sent"),
                            ("first_reply_received", "First reply received"),
                            ("agree_clicked", "Agree clicked"),
                        ],
                        max_length=40,
                    ),
                ),
                ("properties", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "match",
                    models.ForeignKey(
                        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="plusone.match"
                    ),
                ),
                (
                    "post",
                    models.ForeignKey(
                        blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to="plusone.activitypost"
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="productevent",
            index=models.Index(fields=["name", "created_at"], name="event_name_created_idx"),
        ),
        migrations.AddIndex(
            model_name="productevent",
            index=models.Index(fields=["match", "name"], name="event_match_name_idx"),
        ),
    ]
