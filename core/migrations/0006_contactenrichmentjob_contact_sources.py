import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_collectionjob_max_sellers"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="sellercontact",
            name="source",
            field=models.CharField(
                choices=[
                    ("marketplace", "Маркетплейс"),
                    ("dadata", "DaData"),
                    ("yandex_maps", "Яндекс Карты"),
                    ("2gis", "2ГИС"),
                ],
                default="marketplace",
                max_length=16,
                verbose_name="Источник",
            ),
        ),
        migrations.CreateModel(
            name="ContactEnrichmentJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("marketplace", models.CharField(blank=True, choices=[("ozon", "Ozon"), ("wildberries", "Wildberries"), ("yandex_market", "Яндекс.Маркет")], default="", max_length=32)),
                ("status", models.CharField(choices=[("queued", "В очереди"), ("running", "Выполняется"), ("paused", "Нужна проверка 2ГИС"), ("completed", "Завершён"), ("failed", "Ошибка")], db_index=True, default="queued", max_length=16)),
                ("total", models.PositiveIntegerField(default=0)),
                ("processed", models.PositiveIntegerField(default=0)),
                ("matched", models.PositiveIntegerField(default=0)),
                ("contacts_added", models.PositiveIntegerField(default=0)),
                ("errors_count", models.PositiveIntegerField(default=0)),
                ("message", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("collection_job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="contact_enrichments", to="core.collectionjob")),
                ("user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
