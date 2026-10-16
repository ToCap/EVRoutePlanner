from django.urls import path

from interfaces.django_ui import views

urlpatterns = [
    path("", views.index, name="index"),
    path("status/<slug:key>/", views.status, name="status"),
]
