from plusone.ai import moderate_text
from plusone.models import ActivityPost, ProductEvent
from plusone.services.analytics import log_event


def moderate_activity_form(user, form):
    return moderate_text(user, f"{form.cleaned_data['title']} {form.cleaned_data['description']}")


def moderate_activity_text(user, text):
    return moderate_text(user, text)


def save_activity_post_for_user(user, form):
    post = form.save_for_user(user)
    log_event(
        ProductEvent.Name.PUBLISH_CARD,
        user=user,
        post=post,
        properties={
            "activity_type": post.activity_type,
            "expire_minutes": form.cleaned_data.get("expire_minutes"),
        },
    )
    return post


def cancel_activity_post(post):
    post.status = ActivityPost.Status.CANCELLED
    post.save(update_fields=["status", "updated_at"])
    return post
