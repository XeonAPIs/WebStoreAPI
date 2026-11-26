from django.contrib import admin
from django.contrib import messages

from .forms import PluginAdminForm
from .models import ApiEndpoint, Category, Plugin, RequestLog
from .plugin_keys import create_api_key, hash_api_key


admin.site.site_header = "XEON'S API"
admin.site.site_title = "XEON'S API"
admin.site.index_title = "Administration"


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "icon"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ApiEndpoint)
class ApiEndpointAdmin(admin.ModelAdmin):
    list_display = ["name", "category", "http_method", "is_active", "created_at"]
    list_filter = ["category", "http_method", "is_active"]
    search_fields = ["name", "description", "upstream_url"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["created_at"]


@admin.register(Plugin)
class PluginAdmin(admin.ModelAdmin):
    form = PluginAdminForm
    list_display = [
        "name", "category", "http_method", "timeout_seconds", "is_active",
        "requires_api_key", "created_at",
    ]
    list_filter = ["category", "http_method", "is_active", "requires_api_key"]
    list_editable = ["is_active", "requires_api_key"]
    search_fields = ["name", "description", "slug"]
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ["path", "created_by", "created_at", "updated_at"]
    fields = [
        "name", "category", "slug", "description", "http_method", "timeout_seconds", "code",
        "is_active", "requires_api_key", "path", "created_by", "created_at", "updated_at",
    ]
    actions = ["rotate_api_key"]

    def get_list_display(self, request):
        @admin.display(description="API URL")
        def api_url(obj):
            endpoint_url = request.build_absolute_uri(obj.path)
            return f"{endpoint_url}?url=" if obj.http_method == "GET" else endpoint_url

        return (*super().get_list_display(request), api_url)

    def save_model(self, request, obj, form, change):
        issued_key = None
        if not change:
            obj.created_by = request.user
            issued_key = create_api_key()
            obj.api_key_hash = hash_api_key(issued_key)
        super().save_model(request, obj, form, change)
        if issued_key:
            self.message_user(
                request,
                f"Copy this plugin API key now; it will not be shown again: {issued_key}",
                level=messages.WARNING,
            )

    @admin.action(description="Rotate API key for one selected plugin")
    def rotate_api_key(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request, "Select exactly one plugin to rotate its key.", level=messages.ERROR
            )
            return
        plugin = queryset.get()
        issued_key = create_api_key()
        plugin.api_key_hash = hash_api_key(issued_key)
        plugin.save(update_fields=["api_key_hash", "updated_at"])
        self.message_user(
            request,
            f"New key for {plugin.name} (copy now; it will not be shown again): {issued_key}",
            level=messages.WARNING,
        )


@admin.register(RequestLog)
class RequestLogAdmin(admin.ModelAdmin):
    list_display = ["endpoint", "plugin", "ip", "status_code", "timestamp"]
    list_filter = ["status_code", "timestamp"]
    readonly_fields = ["endpoint", "plugin", "ip", "status_code", "timestamp"]

    def has_add_permission(self, _request):
        return False

    def has_change_permission(self, _request, _obj=None):
        return False
