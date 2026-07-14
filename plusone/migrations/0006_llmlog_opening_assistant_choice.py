from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("plusone", "0005_capacity_and_match_close_state"),
    ]

    operations = [
        migrations.AlterField(
            model_name="llmlog",
            name="task_type",
            field=models.CharField(
                choices=[
                    ("parse_post", "Parse post"),
                    ("icebreaker", "Icebreaker"),
                    ("moderation", "Moderation"),
                    ("opening_assistant", "Opening assistant"),
                ],
                max_length=30,
            ),
        ),
    ]
