from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("plusone", "0003_seed_campus_locations"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="activitypost",
            index=models.Index(fields=["user", "status", "expire_time"], name="post_user_status_exp_idx"),
        ),
        migrations.AddIndex(
            model_name="match",
            index=models.Index(fields=["status", "chat_expires_at"], name="match_status_exp_idx"),
        ),
        migrations.AddIndex(
            model_name="match",
            index=models.Index(fields=["poster", "status", "created_at"], name="match_poster_status_idx"),
        ),
        migrations.AddIndex(
            model_name="match",
            index=models.Index(fields=["swiper", "status", "created_at"], name="match_swiper_status_idx"),
        ),
        migrations.AddIndex(
            model_name="chatmessage",
            index=models.Index(fields=["match", "id"], name="chat_match_id_idx"),
        ),
    ]
