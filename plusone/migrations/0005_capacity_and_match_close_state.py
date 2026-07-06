import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("plusone", "0004_performance_indexes"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="activitypost",
            name="capacity",
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="match",
            name="closed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="match",
            name="close_reason",
            field=models.CharField(
                blank=True,
                choices=[
                    ("declined", "Declined by participant"),
                    ("reported", "Reported safety issue"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="match",
            name="closed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="closed_matches",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
