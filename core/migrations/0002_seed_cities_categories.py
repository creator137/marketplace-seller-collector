"""Seed initial cities and default categories."""
from django.db import migrations


CITIES = ["Уфа", "Челябинск", "Екатеринбург"]

SEED_CATEGORIES = {
    "ozon": ["наушники", "смартфоны", "футболка"],
    "wildberries": ["наушники", "смартфоны", "футболка"],
    "yandex_market": ["наушники", "смартфоны", "футболка"],
}


def seed(apps, schema_editor):
    from core.citymatch import normalize_city_name

    City = apps.get_model("core", "City")
    Category = apps.get_model("core", "Category")
    for name in CITIES:
        City.objects.get_or_create(
            name=name, defaults={"normalized": normalize_city_name(name)},
        )
    for marketplace, titles in SEED_CATEGORIES.items():
        for title in titles:
            Category.objects.get_or_create(
                marketplace=marketplace, external_id=title,
                defaults={"title": title.capitalize()},
            )


def unseed(apps, schema_editor):
    City = apps.get_model("core", "City")
    Category = apps.get_model("core", "Category")
    City.objects.filter(name__in=CITIES).delete()
    Category.objects.filter(external_id__in=["наушники", "смартфоны", "футболка"]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
