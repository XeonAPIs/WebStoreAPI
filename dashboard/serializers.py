from rest_framework import serializers

from .models import ApiEndpoint, Category, Plugin


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["name", "slug", "icon"]


class ApiEndpointSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    endpoint_url = serializers.SerializerMethodField()

    class Meta:
        model = ApiEndpoint
        fields = [
            "name", "slug", "description", "http_method", "path",
            "params_schema", "category", "endpoint_url",
        ]

    def get_endpoint_url(self, obj):
        request = self.context.get("request")
        path = f"/api/{obj.category.slug}/{obj.slug}/"
        return request.build_absolute_uri(path) if request else path


class PluginSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)
    endpoint_url = serializers.SerializerMethodField()

    class Meta:
        model = Plugin
        fields = [
            "name", "slug", "description", "http_method", "path",
            "requires_api_key", "category", "endpoint_url",
        ]

    def get_endpoint_url(self, obj):
        request = self.context.get("request")
        return request.build_absolute_uri(obj.path) if request else obj.path
