# -*- coding: utf-8 -*-
"""Pruebas para anonimización determinista extendida (interactuantes, amigas y geo_hints) en HTML/PDF/JSON."""
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


class TestReportAnonymizationExtended(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self._orig_out = utils.OUTPUT_ROOT
        utils.OUTPUT_ROOT = self.tmp_dir / "output"
        utils.OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
        storage.set_db_path(self.tmp_dir / "rep_ext.db")
        storage.init_db()

    def tearDown(self):
        utils.OUTPUT_ROOT = self._orig_out
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_anonymization_masks_interactants_and_friend_evidence_across_html_and_json(self):
        case_id = storage.create_case(title="Caso Reporte Ext", subject_name="Erick Peña")["case"]["id"]
        bundle = empty_bundle("instagram", "erick144", access_method="browser_supervised")
        bundle["profile"] = {"site": "Instagram", "url": "https://instagram.com/erick144/", "username": "erick144", "display_name": "Erick"}
        bundle["posts"].append({
            "external_id": "p1",
            "platform": "instagram",
            "author": "erick144",
            "content": "En la costa con palmeras",
            "published_at": "2026-11-18 10:00:00",
            "published_location": "Cartagena",
            "url": "https://instagram.com/p/p1/",
        })
        bundle["media_items"].append({
            "filename": "playa.jpg",
            "media_type": "image",
            "source_url": "https://instagram.com/playa.jpg",
            "alt_text": "palmeras y balcones coloniales",
            "post_external_id": "p1",
        })
        bundle["comments"].extend([
            {
                "external_id": "c1",
                "post_external_id": "p1",
                "author_handle": "secreto_amigo_real",
                "author_display_name": "Juan Secreto Real",
                "author_profile_url": "https://instagram.com/secreto_amigo_real/",
                "target_handle": "erick144",
                "content": "Buena foto!",
                "published_at": "2026-11-18 12:00:00",
            },
            {
                "external_id": "c2",
                "post_external_id": "p1",
                "author_handle": "secreto_amigo_real",
                "author_display_name": "Juan Secreto Real",
                "author_profile_url": "https://instagram.com/secreto_amigo_real/",
                "target_handle": "erick144",
                "content": "Nos vemos pronto",
                "published_at": "2026-11-19 12:00:00",
            },
        ])
        bundle["interactions"].append({
            "source_handle": "secreto_amigo_real",
            "target_handle": "erick144",
            "kind": "comment",
            "weight": 3.2,
            "evidence_count": 2,
        })
        storage.ingest_connector_bundle(case_id, bundle)

        # Guardar un geo_hint para verificar sección 8
        content = storage.get_case_content(case_id)
        mid = content["media_items"][0]["id"]
        storage.save_media_geo_hints(case_id, mid, [
            {"hint_type": "flora", "value": "palmeras", "confidence": 0.72, "evidence_quote": "palmeras en alt_text", "source": "local_context"}
        ])

        res = case_report.generate_case_report(case_id, anonymize=True, include_quotes=True, include_gallery=True)
        self.assertIsNotNone(res)
        out_folder = utils.OUTPUT_ROOT / res["folder"]
        html_txt = (out_folder / "resumen.html").read_text(encoding="utf-8")
        json_data = json.loads((out_folder / "caso_completo.json").read_text(encoding="utf-8"))

        # Verificar que las nuevas claves existen en JSON y no filtran el handle/nombre real
        self.assertIn("interactants", json_data)
        self.assertIn("friend_evidence", json_data)
        self.assertIn("geo_hints", json_data)
        self.assertNotIn("secreto_amigo_real", html_txt)
        self.assertNotIn("Juan Secreto Real", html_txt)
        self.assertNotIn("secreto_amigo_real", json.dumps(json_data, ensure_ascii=False))
        self.assertIn("Tercero_01", html_txt)
        self.assertIn("Interactuantes por publicación", html_txt)
        self.assertIn("Pistas de Ubicación Visual", html_txt)


if __name__ == "__main__":
    unittest.main()
