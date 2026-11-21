# -*- coding: utf-8 -*-
"""Pruebas para Módulo B: Proximidad visual (dHash entre perfiles), score SNA e historial consolidado."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import browser_agent, sna_graph, storage  # noqa: E402
from modules.connectors.common import empty_bundle  # noqa: E402
from modules.vision_analysis import compare_media_between_profiles  # noqa: E402


class TestVisualProximity(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        storage.set_db_path(Path(self._tmp.name) / "vprox.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_compare_media_between_profiles_respects_hamming_threshold(self):
        media_a = [
            {"id": 1, "dhash": "ffffffffffffffff", "source_url": "https://img/a1.jpg", "filename": "a1.jpg", "published_at": "2026-11-18"},
        ]
        media_b = [
            {"id": 2, "dhash": "fffffffffffffff0", "source_url": "https://img/b1.jpg", "filename": "b1.jpg", "published_at": "2026-11-18"},  # dist=4
            {"id": 3, "dhash": "0000000000000000", "source_url": "https://img/b2.jpg", "filename": "b2.jpg", "published_at": "2026-11-19"},  # dist=64
        ]
        matches = compare_media_between_profiles(media_a, media_b, hamming_threshold=10)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["media_b_id"], 2)
        self.assertEqual(matches[0]["hamming_distance"], 4)

    def test_compute_visual_proximity_score_and_sna_node_exposure(self):
        score = sna_graph.compute_visual_proximity_score(dhash_matches_count=1, shared_active_days=2, shared_ner_count=2)
        self.assertGreater(score, 30.0)
        self.assertLessEqual(score, 100.0)

        case_id = storage.create_case(title="Caso SNA Vis", subject_name="Erick")["case"]["id"]
        bundle = empty_bundle("instagram", "erick144", access_method="browser_supervised")
        bundle["profile"] = {"site": "Instagram", "url": "https://instagram.com/erick144/", "username": "erick144", "display_name": "Erick"}
        bundle["interactions"].append({"source_handle": "laura_amiga", "target_handle": "erick144", "kind": "comment", "weight": 2.0, "evidence_count": 2})
        storage.ingest_connector_bundle(case_id, bundle)
        storage.update_interaction_visual_score(case_id, "laura_amiga", 55.0)
        sna = storage.compute_and_save_case_sna(case_id)
        self.assertGreaterEqual(sna["close_circle"][0]["visual_proximity_score"], 55.0)

    def test_get_case_friend_evidence_builds_consolidated_history(self):
        case_id = storage.create_case(title="Caso Hist", subject_name="Erick")["case"]["id"]
        bundle = empty_bundle("instagram", "erick144", access_method="browser_supervised")
        bundle["profile"] = {"site": "Instagram", "url": "https://instagram.com/erick144/", "username": "erick144", "display_name": "Erick"}
        bundle["comments"].extend([
            {"external_id": "c1", "author_handle": "laura_amiga", "target_handle": "erick144", "content": "Hola!", "published_at": "2026-11-18 10:00:00"},
            {"external_id": "c2", "author_handle": "laura_amiga", "target_handle": "erick144", "content": "Qué foto!", "published_at": "2026-11-19 11:00:00"},
        ])
        bundle["interactions"].append({"source_handle": "laura_amiga", "target_handle": "erick144", "kind": "comment", "weight": 3.2, "evidence_count": 2})
        storage.ingest_connector_bundle(case_id, bundle)

        ev = browser_agent.get_case_friend_evidence(case_id)
        self.assertEqual(len(ev["candidates"]), 1)
        self.assertEqual(ev["consolidated_history"][0]["handle"], "laura_amiga")
        self.assertEqual(len(ev["consolidated_history"][0]["history"]), 2)


if __name__ == "__main__":
    unittest.main()
