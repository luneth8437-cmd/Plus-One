from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("discover/", views.discover, name="discover"),
    path("login/", views.start_anonymous_session, name="login"),
    path("identity/reset/", views.reset_anonymous_identity, name="reset_anonymous_identity"),
    path("session/", views.profile_setup, name="session"),
    path("profile/setup/", views.profile_setup, name="profile_setup"),
    path("about/", views.about, name="about"),
    path("create/", views.create_post, name="create_post"),
    path("posts/<int:post_id>/", views.post_detail, name="post_detail"),
    path("posts/<int:post_id>/edit/", views.edit_post, name="edit_post"),
    path("posts/<int:post_id>/swipe/", views.swipe_post, name="swipe_post"),
    path("posts/<int:post_id>/undo-pass/", views.undo_pass, name="undo_pass"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("matches/", views.dashboard, name="matches_dashboard"),
    path("chat/<int:match_id>/", views.chat, name="chat"),
    path("chat/<int:match_id>/messages/", views.chat_messages, name="chat_messages"),
]
