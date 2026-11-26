import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0002_seed_demo_api"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Plugin",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120)),
                ("slug", models.SlugField(max_length=140)),
                ("description", models.CharField(blank=True, max_length=500)),
                ("http_method", models.CharField(choices=[("GET", "GET"), ("POST", "POST")], default="GET", max_length=8)),
                ("code", models.TextField(help_text="Trusted admins only: code runs in a limited child process, not a secure sandbox. It can still access service files and the network.")),
                ("is_active", models.BooleanField(default=True)),
                ("requires_api_key", models.BooleanField(default=False, help_text="When enabled, callers must send the generated key in X-API-Key or a Bearer token.")),
                ("api_key_hash", models.CharField(editable=False, max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="plugins", to="dashboard.category")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["category__name", "name"],
            },
        ),
        migrations.AddConstraint(
            model_name="plugin",
            constraint=models.UniqueConstraint(fields=("category", "slug"), name="unique_plugin_per_category"),
        ),
        migrations.AddField(
            model_name="requestlog",
            name="plugin",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="request_logs", to="dashboard.plugin"),
        ),
    ]
