from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.conf import settings
from django.views.static import serve
from django.urls import re_path
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("django-rq/", include("django_rq.urls")),
    re_path(r"^static/(?P<path>.*)$", serve, {"document_root": settings.STATIC_ROOT}),
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(template_name="core/login.html"),
        name="login",
    ),
    path(
        "logout/",
        auth_views.LogoutView.as_view(),
        name="logout",
    ),
    path("", include("core.urls")),
]

# Serve collected assets for the local deployment as well as DEBUG mode. A
# reverse proxy can take over this path in production without changing URLs.
