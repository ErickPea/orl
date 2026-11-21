# -*- coding: utf-8 -*-
"""Pruebas para Fase 0 y 2: Aislamiento estricto del contenido de amigos respecto al sujeto."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import web_app  # noqa: E402
from modules import storage  # noqa: E402
from modules.connectors.common import empty_bundle  # noqa: E402
from modules.subject_profile import compute_subject_profile  # noqa: E402


class TestFriendIsolation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        storage.set_db_path(Path(self._tmp.name) / "isolation.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_friend_scope_default_false_and_zero_leak_into_subject_views(self):
        case_obj = storage.create_case(title="Caso Aislamiento", subject_name="Erick")["case"]
        case_id = case_obj["id"]
        self.assertEqual(int(case_obj.get("friend_scope_enabled", 0)), 0)

        # Contenido del sujeto
        subj_bundle = empty_bundle("instagram", "erick144", access_method="browser_supervised")
        subj_bundle["posts"].append({"external_id": "sp1", "author": "erick144", "content": "Café en Bogotá"})
        storage.ingest_connector_bundle(case_id, subj_bundle)

        # Amigo aislado con post, foto y ubicación propia
        fr = web_app.api_case_add_friend(
            case_id,
            web_app.FriendAddRequest(url="https://www.instagram.com/amigo_aislado/", platform="instagram", label="Amigo"),
        )["friend"]
        fr_bundle = empty_bundle("instagram", "amigo_aislado", access_method="browser_supervised")
        fr_bundle["posts"].append({"external_id": "fp1", "author": "amigo_aislado", "content": "SECRETO_AMIGO_TOKIO"})
        fr_bundle["media_items"].append({
            "filename": "tokio_secret.jpg",
            "media_type": "image",
            "source_url": "https://instagram.com/tokio.jpg",
            "alt_text": "SECRETO_AMIGO_TOKIO",
            "dhash": "ffffffffffffffff",
            "post_external_id": "fp1",
            "published_at": "2026-11-18 10:00:00",
        })
        storage.ingest_friend_bundle(case_id, fr["id"], fr_bundle)
        storage.save_location_history_entry(
            case_id=case_id,
            owner_type="friend",
            owner_id=fr["id"],
            city="Tokio",
            country="Japón",
            confidence=0.9,
            published_at="2026-11-18 10:00:00",
        )

        # Verificar que NADA del amigo aparece en vistas del sujeto
        subj_content = storage.get_case_content(case_id)
        self.assertEqual(len(subj_content["posts"]), 1)
        self.assertEqual(len(subj_content["media_items"]), 0)

        subj_corpus = storage.get_case_corpus_for_nlp(case_id)
        self.assertNotIn("SECRETO_AMIGO_TOKIO", str(subj_corpus))

        subj_loc = storage.list_location_history(case_id, owner_type="subject")
        self.assertEqual(len(subj_loc), 0)

        prof = compute_subject_profile(case_id, persist=True)
        self.assertNotIn("Tokio", str(prof))


if __name__ == "__main__":
    unittest.main()
