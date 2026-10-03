from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("plusone", "0017_meetup_arrival_coordination")]

    operations = [
        migrations.RemoveConstraint(model_name="match", name="unique_match_per_post_swiper"),
        migrations.AddField(model_name="match", name="retry_of", field=models.OneToOneField(
            blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
            related_name="waiting_retry", to="plusone.match")),
        migrations.AddConstraint(model_name="match", constraint=models.UniqueConstraint(
            fields=("post", "swiper"), condition=models.Q(status__in=["waiting", "chatting"]),
            name="unique_live_match_post_swiper")),
    ]
