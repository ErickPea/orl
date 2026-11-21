# -*- coding: utf-8 -*-
"""Pruebas para Módulo C: Extractores GeoHints, fusión EXIF+NER, ranking y lenguaje probabilístico."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.geo_inference import infer_case_locations, nominatim_enabled  # noqa: E402
from modules.vision_analysis import extract_geo_hints  # noqa: E402


class TestGeoHintsInference(unittest.TestCase):
    def test_extract_geo_hints_with_mock_vlm_and_local_context(self):
        def mock_vlm(img_path, prompt):
            return json.dumps({
                "flora": [{"value": "palmeras", "confidence": 0.85, "evidence_quote": "Varias palmeras costeras al fondo"}],
                "architecture": [{"value": "balcones coloniales", "confidence": 0.78, "evidence_quote": "Fachada con balcones de madera"}],
                "ocr": [{"value": "Cartagena", "confidence": 0.82, "evidence_quote": "Letrero visible: Cartagena"}],
                "climate_light": [{"value": "soleado", "confidence": 0.65, "evidence_quote": "Luz solar intensa"}],
                "distinctive_objects": [],
            })

        res = extract_geo_hints(image_path=None, vlm_client=mock_vlm, caption="Paseo por la ciudad amurallada")
        self.assertGreaterEqual(len(res["flora"]), 1)
        self.assertGreaterEqual(len(res["architecture"]), 1)
        self.assertEqual(res["hints"][0]["source"], "vlm")
        self.assertTrue(all(0.0 <= h["confidence"] <= 1.0 for h in res["hints"]))

    def test_exif_gps_has_priority_and_probabilistic_language(self):
        exif_res = extract_geo_hints(
            exif_gps={"lat": 4.6097, "lon": -74.0817},
            caption="Caminando cerca de TransMilenio",
        )
        inferred = infer_case_locations(
            geo_hints=exif_res["hints"],
            media_items=[{"id": 1, "filename": "foto.jpg", "metadata": {"gps": {"lat": 4.6097, "lon": -74.0817}}}],
            posts=[{"published_location": "Bogotá, Colombia"}],
            nlp_data={"ner": {"places": [{"text": "Bogotá", "count": 2}]}},
            use_nominatim=False,
        )
        self.assertFalse(inferred["nominatim_enabled"])
        cands = inferred["candidates"]
        self.assertGreaterEqual(len(cands), 2)
        self.assertEqual(cands[0]["source"], "exif")
        self.assertTrue(cands[0]["has_exif"])
        for c in cands:
            stmt = c["probabilistic_statement"].lower()
            self.assertTrue("compatible con" in stmt or "posible" in stmt)

    def test_nominatim_disabled_by_default(self):
        self.assertFalse(nominatim_enabled())


if __name__ == "__main__":
    unittest.main()
