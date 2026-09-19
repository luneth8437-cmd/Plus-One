from django.db import migrations


def recover_references(apps, schema_editor):
    Event = apps.get_model("plusone", "ProductEvent")
    once_match = {"match_created", "chat_started", "both_agreed", "first_message_sent", "first_reply_received"}
    once_user = {"agree_clicked", "meetup_confirmed"}
    # Only copy attribution that still exists. Deleted historical relations
    # remain unknown; no invented timestamps or reconstructed business events.
    for event in Event.objects.select_related("post", "match", "match__post").order_by("pk").iterator(chunk_size=500):
        post = event.post or (event.match.post if event.match else None)
        event.post_reference = post.pk if post else None
        event.post_created_at = post.created_at if post else None
        event.match_reference = event.match_id
        event.match_created_at = event.match.created_at if event.match else None
        key = None
        if event.name == "publish_card" and post:
            key = f"{event.name}:post:{post.pk}"
        elif event.name in once_match and event.match_id:
            key = f"{event.name}:match:{event.match_id}"
        elif event.name in once_user and event.match_id and event.user_id:
            key = f"{event.name}:match:{event.match_id}:user:{event.user_id}"
        if key and not Event.objects.filter(event_key=key).exists():
            event.event_key = key
        event.save(update_fields=["post_reference", "post_created_at", "match_reference", "match_created_at", "event_key"])


class Migration(migrations.Migration):
    dependencies = [("plusone", "0010_reliable_match_lifecycle")]
    operations = [migrations.RunPython(recover_references, migrations.RunPython.noop)]
