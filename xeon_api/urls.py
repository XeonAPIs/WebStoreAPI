from django.contrib import admin
from django.urls import include, path
from django.contrib.auth import views as auth_views
from dashboard import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("login/", auth_views.LoginView.as_view(
        template_name="registration/login.html"
    ), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", views.dashboard, name="dashboard"),
    path("docs/", views.documentation, name="docs"),
    path("upload/", views.upload_api, name="upload_api"),
    path("apis/<int:pk>/edit/", views.edit_api, name="edit_api"),
    path("apis/<int:pk>/delete/", views.delete_api, name="delete_api"),
    path("api/stats/", views.stats_api, name="stats_api"),
    path("api/<slug:category>/<slug:slug>/", views.proxy_endpoint, name="proxy_endpoint"),
    path("", include("dashboard.urls")),
]
