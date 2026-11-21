# -*- coding: utf-8 -*-
"""Pruebas para Módulo A: Registro nominal de interactuantes."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import storage  # noqa: E402
from modules.connectors.common import add_interactant, empty_bundle  # noqa: E402


class TestInteractantsCapture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        storage.set_db_path(Path(self._tmp.name) / "interactants.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_comment_with_visible_name_assigns_non_null_interactant_id(self):
        case_id = storage.create_case(title="Caso Interactuantes", subject_name="Erick")["case"]["id"]
        bundle = empty_bundle("instagram", "erick144", access_method="browser_supervised")
        bundle["posts"].append({
            "external_id": "p1",
            "platform": "instagram",
            "author": "erick144",
            "content": "Foto en el parque",
            "published_at": "2026-11-18 10:00:00",
            "url": "https://instagram.com/p/p1/",
        })
        bundle["comments"].append({
            "external_id": "c1",
            "post_external_id": "p1",
            "author_handle": "",
            "author_display_name": "Laura Gómez",
            "author_profile_url": "https://instagram.com/lauragomez/",
            "target_handle": "erick144",
            "content": "Qué buena foto!",
            "published_at": "2026-11-18 11:00:00",
        })
        storage.ingest_connector_bundle(case_id, bundle)
        content = storage.get_case_content(case_id)
        self.assertEqual(len(content["comments"]), 1)
        self.assertIsNotNone(content["comments"][0].get("interactant_id"))

        its = storage.list_case_interactants(case_id)
        self.assertEqual(len(its), 1)
        self.assertEqual(its[0]["display_name"], "Laura Gómez")
        self.assertEqual(its[0]["comments_count"], 1)

    def test_interactants_deduplication_and_friend_candidate_threshold(self):
        case_id = storage.create_case(title="Caso Dedup", subject_name="Erick")["case"]["id"]
        bundle = empty_bundle("instagram", "erick144", access_method="browser_supervised")
        add_interactant(bundle["interactants"], "instagram", "carlos_dev", "Carlos Dev", "https://instagram.com/carlos_dev/", "2026-11-18 09:00:00")
        add_interactant(bundle["interactants"], "instagram", "carlos_dev", "Carlos Dev", "https://instagram.com/carlos_dev/", "2026-11-19 09:00:00")
        self.assertEqual(len(bundle["interactants"]), 1)

        bundle["comments"].extend([
            {"external_id": "c1", "author_handle": "carlos_dev", "author_display_name": "Carlos Dev", "target_handle": "erick144", "content": "Primero", "published_at": "2026-11-18 09:00:00"},
            {"external_id": "c2", "author_handle": "carlos_dev", "author_display_name": "Carlos Dev", "target_handle": "erick144", "content": "Segundo", "published_at": "2026-11-19 09:00:00"},
        ])
        storage.ingest_connector_bundle(case_id, bundle)
        its = storage.list_case_interactants(case_id)
        self.assertEqual(len(its), 1)
        self.assertEqual(its[0]["comments_count"], 2)
        self.assertTrue(its[0]["is_friend_candidate"])

    def test_api_case_interactants_endpoint(self):
        import web_app
        case_id = storage.create_case(title="Caso API", subject_name="Ana")["case"]["id"]
        storage.upsert_interactant(case_id, "mastodon", "marta_ok", "Marta OK", "https://mastodon.social/@marta_ok", "2026-11-19 12:00:00")
        res = web_app.api_case_interactants(case_id)
        self.assertEqual(res["count"], 1)
        self.assertEqual(res["interactants"][0]["handle"], "marta_ok")


if __name__ == "__main__":
    unittest.main()
