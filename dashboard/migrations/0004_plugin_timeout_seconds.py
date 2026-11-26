import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0003_plugin"),
    ]

    operations = [
        migrations.AddField(
            model_name="plugin",
            name="timeout_seconds",
            field=models.PositiveSmallIntegerField(
                default=15,
                help_text="Maximum execution time in seconds (1-120).",
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(120),
                ],
            ),
        ),
        migrations.AddConstraint(
            model_name="plugin",
            constraint=models.CheckConstraint(
                condition=models.Q(timeout_seconds__gte=1, timeout_seconds__lte=120),
                name="plugin_timeout_seconds_range",
            ),
        ),
    ]
