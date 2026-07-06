from datetime import timedelta

from django import forms
from django.utils import timezone

from .models import ActivityPost, CampusLocation


class ActivityAssistForm(forms.Form):
    raw_text = forms.CharField(
        label="Tell Plus One what you want to do",
        widget=forms.Textarea(
            attrs={
                "rows": 4,
                "placeholder": "Example: Tonight around 7 I want to go to the basketball game at the sports hall.",
            }
        ),
    )


class ActivityPostForm(forms.ModelForm):
    expire_minutes = forms.IntegerField(
        label="Post expires after",
        min_value=5,
        max_value=180,
        initial=45,
        help_text="Minutes. Default is 45.",
    )
    class Meta:
        model = ActivityPost
        fields = ["title", "description", "activity_type", "location", "start_time", "expire_minutes"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "start_time": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["activity_type"].choices = [("", "Activity"), *ActivityPost.ActivityType.choices]
        self.fields["location"].queryset = CampusLocation.objects.order_by("area", "name")
        self.fields["location"].empty_label = "Campus location"
        self.fields["title"].widget.attrs.setdefault("placeholder", "Basketball game tonight")
        self.fields["description"].widget.attrs.setdefault("placeholder", "Looking for someone to join for a quick vibe check first.")

    def clean_start_time(self):
        start_time = self.cleaned_data["start_time"]
        if timezone.is_naive(start_time):
            start_time = timezone.make_aware(start_time, timezone.get_current_timezone())
        # Allow a small grace window so a user can submit a just-filled form
        # without racing the server clock by a few seconds.
        if start_time < timezone.now() - timedelta(minutes=5):
            raise forms.ValidationError("Start time cannot be in the past.")
        return start_time

    def save_for_user(self, user):
        post = super().save(commit=False)
        post.user = user
        post.expire_time = timezone.now() + timedelta(minutes=self.cleaned_data["expire_minutes"])
        post.capacity = 1
        post.status = ActivityPost.Status.ACTIVE
        post.save()
        return post


class ChatMessageForm(forms.Form):
    message = forms.CharField(
        label="Message",
        max_length=500,
        widget=forms.TextInput(attrs={"placeholder": "Type fast... this chat expires soon."}),
    )
