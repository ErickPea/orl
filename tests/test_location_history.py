# -*- coding: utf-8 -*-
"""Pruebas para Fase 1: Selección de foto individual, override manual e historial cronológico de ubicaciones."""
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


class TestLocationHistory(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        storage.set_db_path(Path(self._tmp.name) / "loc_hist.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_single_photo_geo_investigate_and_manual_override_precedence(self):
        case_id = storage.create_case(title="Caso Geo Foto", subject_name="Erick")["case"]["id"]
        bundle = empty_bundle("instagram", "erick144", access_method="browser_supervised")
        bundle["media_items"].extend([
            {
                "filename": "bogota_foto.jpg",
                "media_type": "image",
                "source_url": "https://instagram.com/f1.jpg",
                "alt_text": "Caminando cerca de TransMilenio en Chapinero",
                "published_at": "2026-11-10 10:00:00",
            },
            {
                "filename": "cartagena_foto.jpg",
                "media_type": "image",
                "source_url": "https://instagram.com/f2.jpg",
                "alt_text": "Balcones coloniales y palmeras",
                "published_at": "2026-11-15 15:00:00",
            },
        ])
        storage.ingest_connector_bundle(case_id, bundle)
        media = storage.get_case_content(case_id)["media_items"]
        m1 = [m for m in media if m["filename"] == "bogota_foto.jpg"][0]
        m2 = [m for m in media if m["filename"] == "cartagena_foto.jpg"][0]

        # Investigar solo m1 (sin red/VLM en prueba unitaria)
        r1 = web_app.api_case_media_geo_investigate(case_id, m1["id"], web_app.MediaGeoInvestigateRequest(use_vlm=False))
        self.assertEqual(r1["media_id"], m1["id"])
        self.assertGreaterEqual(len(r1["candidates"]), 1)

        # Investigar m2 y luego aplicar override manual
        web_app.api_case_media_geo_investigate(case_id, m2["id"], web_app.MediaGeoInvestigateRequest(use_vlm=False))
        ov = web_app.api_case_media_geo_investigate(
            case_id,
            m2["id"],
            web_app.MediaGeoInvestigateRequest(manual_override=True, city="Santa Marta", region="Magdalena", country="Colombia"),
        )
        self.assertTrue(ov["manual_override"])
        # Override manual resuelve coordenadas aproximadas para aparecer en el mapa
        self.assertIsNotNone(ov["location"]["lat"])

        # Historial ordenado cronológicamente por published_at ascendente y con override prevaleciendo
        hist = web_app.api_case_location_history(case_id)
        self.assertEqual(hist["count"], 2)
        self.assertEqual(hist["history"][0]["media_id"], m1["id"])
        self.assertEqual(hist["history"][1]["media_id"], m2["id"])
        self.assertEqual(hist["history"][1]["city"], "Santa Marta")
        self.assertEqual(hist["history"][1]["is_manual_override"], 1)

        # Filtro por source=manual
        hist_man = web_app.api_case_location_history(case_id, source="manual")
        self.assertEqual(hist_man["count"], 1)
        self.assertEqual(hist_man["history"][0]["city"], "Santa Marta")

        # Exportación CSV
        csv_resp = web_app.api_case_location_history(case_id, format="csv")
        self.assertIn("Santa Marta", csv_resp.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
