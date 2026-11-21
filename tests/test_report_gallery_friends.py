# -*- coding: utf-8 -*-
"""Pruebas para Fase 3: Galería de fotos con ubicación, nombres de amigos y anonimización configurable."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import case_report, storage, utils  # noqa: E402
from modules.connectors.common import empty_bundle  # noqa: E402


class TestReportGalleryFriends(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self._orig_out = utils.OUTPUT_ROOT
        utils.OUTPUT_ROOT = self.tmp_dir / "output"
        utils.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        storage.set_db_path(self.tmp_dir / "rep_gal.db")
        storage.init_db()

    def tearDown(self):
        utils.OUTPUT_ROOT = self._orig_out
        storage.set_db_path(None)
        self._tmp.cleanup()

    def _seed_case(self) -> str:
        cid = storage.create_case(title="Caso Galeria Amigos", subject_name="Erick")["case"]["id"]
        b = empty_bundle("instagram", "erick144", access_method="browser_supervised")
        b["profile"] = {"site": "Instagram", "url": "https://instagram.com/erick144/", "username": "erick144", "display_name": "Erick"}
        b["posts"].append({"external_id": "p1", "author": "erick144", "content": "Foto en Bogotá", "url": "https://instagram.com/p/p1/", "published_at": "2026-11-18"})
        b["media_items"].append({"filename": "bog.jpg", "media_type": "image", "source_url": "https://instagram.com/bog.jpg", "alt_text": "Bogotá", "post_external_id": "p1", "published_at": "2026-11-18"})
        b["comments"].append({"external_id": "c1", "post_external_id": "p1", "author_handle": "valeria_confirmada", "author_display_name": "Valeria Confirmada", "target_handle": "erick144", "content": "Qué foto!"})
        b["interactions"].extend([
            {"source_handle": "valeria_confirmada", "target_handle": "erick144", "kind": "comment", "weight": 2.0, "evidence_count": 2},
            {"source_handle": "erick144", "target_handle": "valeria_confirmada", "kind": "reply", "weight": 2.0, "evidence_count": 1},
        ])
        storage.ingest_connector_bundle(cid, b)
        mid = storage.get_case_content(cid)["media_items"][0]["id"]
        storage.save_location_history_entry(cid, media_id=mid, source="manual", city="Bogotá", region="Cundinamarca", country="Colombia", confidence=1.0, is_manual_override=True, published_at="2026-11-18")
        return cid

    def test_report_gallery_and_configurable_friend_anonymization(self):
        cid = self._seed_case()

        # 1) Por defecto anonymize=True, reveal_confirmed_friends=False -> oculta valeria_confirmada
        r_anon = case_report.generate_case_report(cid, anonymize=True, reveal_confirmed_friends=False)
        j_anon = json.loads((utils.OUTPUT_ROOT / r_anon["folder"] / "caso_completo.json").read_text(encoding="utf-8"))
        h_anon = (utils.OUTPUT_ROOT / r_anon["folder"] / "resumen.html").read_text(encoding="utf-8")
        self.assertIn("media_gallery", j_anon)
        self.assertEqual(j_anon["media_gallery"][0]["possible_location"], "Bogotá, Cundinamarca, Colombia")
        self.assertIn("friends_circle", j_anon)
        self.assertNotIn("valeria_confirmada", h_anon)

        # Verificar grid HTML con filtros y miniatura
        self.assertIn("galFilterAuthor", h_anon)
        self.assertIn("galFilterLoc", h_anon)
        self.assertIn("galFilterConf", h_anon)
        self.assertIn('<img src="https://instagram.com/bog.jpg"', h_anon)
        self.assertNotIn("local_path", json.dumps(j_anon["media_gallery"]))

        # 2) Con reveal_confirmed_friends=True -> revela el nombre del amigo confirmado y registra auditoría
        r_rev = case_report.generate_case_report(cid, anonymize=True, reveal_confirmed_friends=True)
        j_rev = json.loads((utils.OUTPUT_ROOT / r_rev["folder"] / "caso_completo.json").read_text(encoding="utf-8"))
        h_rev = (utils.OUTPUT_ROOT / r_rev["folder"] / "resumen.html").read_text(encoding="utf-8")
        self.assertTrue(j_rev["governance"]["reveal_confirmed_friends"])
        self.assertIn("valeria_confirmada", h_rev)
        self.assertIn("Nombres de amigos confirmados revelados por decisión del analista", h_rev)


if __name__ == "__main__":
    unittest.main()
