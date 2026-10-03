from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("plusone", "0016_safety_presence_and_product_events")]

    operations = [
        migrations.AddField(
            model_name="match", name=f"{actor}_arrival_eta",
            field=models.DateTimeField(blank=True, null=True),
        )
        for actor in ("poster", "swiper")
    ] + [
        migrations.AddField(
            model_name="match", name=f"{actor}_arrival_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        )
        for actor in ("poster", "swiper")
    ] + [
        migrations.AddField(
            model_name="match", name=f"{actor}_coordination_signal",
            field=models.CharField(
                max_length=20, blank=True, default="",
                choices=[("at_point", "At the agreed meeting point"), ("at_entrance", "At the entrance of the agreed place"),
                         ("cant_find", "At the agreed point but cannot find you")],
            ),
        )
        for actor in ("poster", "swiper")
    ]
