from django.db import migrations


def create_demo_endpoint(apps, schema_editor):
    database = schema_editor.connection.alias
    Category = apps.get_model("dashboard", "Category")
    ApiEndpoint = apps.get_model("dashboard", "ApiEndpoint")

    category, _ = Category.objects.using(database).get_or_create(
        slug="demo",
        defaults={"name": "Demo", "icon": "◇"},
    )
    ApiEndpoint.objects.using(database).get_or_create(
        category=category,
        slug="example-todo",
        defaults={
            "name": "Example Todo",
            "description": "A sample JSON API response powered by JSONPlaceholder.",
            "http_method": "GET",
            "path": "/api/demo/example-todo/",
            "upstream_url": "https://jsonplaceholder.typicode.com/todos/1",
            "params_schema": {},
            "is_active": True,
        },
    )


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_demo_endpoint, migrations.RunPython.noop),
    ]
