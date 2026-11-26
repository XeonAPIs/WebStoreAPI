from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.text import slugify

from .plugin_runner import MAX_PLUGIN_TIMEOUT_SECONDS, PLUGIN_TIMEOUT_SECONDS
from .security import validate_upstream_url


class Category(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=90, unique=True)
    icon = models.CharField(max_length=40, default="◇")

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "categories"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class ApiEndpoint(models.Model):
    class HttpMethod(models.TextChoices):
        GET = "GET", "GET"
        POST = "POST", "POST"

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="endpoints")
    description = models.TextField(blank=True)
    http_method = models.CharField(max_length=8, choices=HttpMethod.choices, default=HttpMethod.GET)
    path = models.CharField(max_length=255, blank=True)
    upstream_url = models.URLField(max_length=1000)
    params_schema = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["category__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["category", "slug"], name="unique_endpoint_per_category")
        ]

    def clean(self):
        try:
            validate_upstream_url(self.upstream_url)
        except ValueError as exc:
            raise ValidationError({"upstream_url": str(exc)}) from exc
        if not isinstance(self.params_schema, dict):
            raise ValidationError({"params_schema": "Parameters must be a JSON object."})
        if self.category_id and self.slug and Plugin.objects.filter(
            category_id=self.category_id, slug=self.slug
        ).exists():
            raise ValidationError({"slug": "A plugin already uses this category and endpoint path."})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        self.path = f"/api/{self.category.slug}/{self.slug}/"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Plugin(models.Model):
    class HttpMethod(models.TextChoices):
        GET = "GET", "GET"
        POST = "POST", "POST"

    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=140)
    category = models.ForeignKey(Category, on_delete=models.PROTECT, related_name="plugins")
    description = models.CharField(max_length=500, blank=True)
    http_method = models.CharField(max_length=8, choices=HttpMethod.choices, default=HttpMethod.GET)
    timeout_seconds = models.PositiveSmallIntegerField(
        default=PLUGIN_TIMEOUT_SECONDS,
        validators=[
            MinValueValidator(1),
            MaxValueValidator(MAX_PLUGIN_TIMEOUT_SECONDS),
        ],
        help_text=f"Maximum execution time in seconds (1-{MAX_PLUGIN_TIMEOUT_SECONDS}).",
    )
    code = models.TextField(
        help_text=(
            "Trusted admins only: code runs in a limited child process, not a secure sandbox. "
            "It can still access service files and the network."
        )
    )
    is_active = models.BooleanField(default=True)
    requires_api_key = models.BooleanField(
        default=False,
        help_text="When enabled, callers must send the generated key in X-API-Key or a Bearer token.",
    )
    api_key_hash = models.CharField(max_length=64, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category__name", "name"]
        constraints = [
            models.UniqueConstraint(fields=["category", "slug"], name="unique_plugin_per_category"),
            models.CheckConstraint(
                condition=models.Q(
                    timeout_seconds__gte=1,
                    timeout_seconds__lte=MAX_PLUGIN_TIMEOUT_SECONDS,
                ),
                name="plugin_timeout_seconds_range",
            ),
        ]

    @property
    def path(self):
        if not self.category_id or not self.slug:
            return ""
        return f"/api/{self.category.slug}/{self.slug}/"

    def clean(self):
        if self.category_id and self.slug and ApiEndpoint.objects.filter(
            category_id=self.category_id, slug=self.slug
        ).exists():
            raise ValidationError({"slug": "An API endpoint already uses this category and path."})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class RequestLog(models.Model):
    endpoint = models.ForeignKey(ApiEndpoint, on_delete=models.SET_NULL, null=True, related_name="request_logs")
    plugin = models.ForeignKey(
        Plugin, on_delete=models.SET_NULL, null=True, blank=True, related_name="request_logs"
    )
    ip = models.GenericIPAddressField()
    status_code = models.PositiveSmallIntegerField()
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        owner = self.plugin or self.endpoint or "Deleted endpoint"
        return f"{owner} {self.status_code} {self.timestamp:%Y-%m-%d %H:%M}"
