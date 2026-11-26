from django.urls import path
from . import views

urlpatterns = [
    path("api/endpoints/", views.endpoint_list, name="endpoint_list"),
]
