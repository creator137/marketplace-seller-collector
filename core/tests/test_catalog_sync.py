import io
import json
import zipfile

from django.test import SimpleTestCase

from core.management.commands.sync_catalogs import (
    flatten_ozon_categories,
    flatten_wb_categories,
    parse_oktmo_cities,
)


class CatalogSyncParserTest(SimpleTestCase):
    def test_oktmo_city_rows_are_deduplicated(self):
        data = (
            '"01";"1";"0";"0";"0";"2";"г Уфа";;;;;;\n'
            '"01";"2";"0";"0";"0";"1";"район";"г Уфа";;;;;\n'
            '"01";"3";"0";"0";"0";"2";"с Ивановка";;;;;;\n'
        ).encode()
        self.assertEqual(parse_oktmo_cities(data), ["Уфа"])

    def test_wb_tree_is_recursive(self):
        tree = [{"name": "Top", "childs": [{
            "name": "Leaf", "shard": "x", "query": "leaf-query",
        }]}]
        self.assertEqual(flatten_wb_categories(tree), {"x|leaf-query": "Leaf"})

    def test_ozon_archive_reads_category_urls(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(
                "repo/data/categories/1.json",
                json.dumps({"data": {"title": "Телефоны", "url": "/category/telefony-1/"}}),
            )
        self.assertEqual(
            flatten_ozon_categories(output.getvalue()),
            {"/category/telefony-1/": "Телефоны"},
        )
