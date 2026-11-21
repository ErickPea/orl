# -*- coding: utf-8 -*-
"""Pruebas unitarias e integración para la Fase 3 (Galería, dHash y Análisis Visual)."""
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import storage  # noqa: E402
from modules.vision_analysis import (  # noqa: E402
    analyze_image_bytes,
    compute_dhash_from_image,
    hamming_distance,
    summarize_case_vision,
    vision_capabilities,
)


def _make_sample_image(kind: str = "mountain", size=(240, 180), fmt="PNG") -> bytes:
    """Genera una imagen sintética con gradientes reales (como una foto) para probar dHash y paleta."""
    img = Image.new("RGB", size)
    w, h = size
    px = img.load()
    for y in range(h):
        for x in range(w):
            if kind == "mountain":
                # Cielo con gradiente diagonal + silueta de montaña con textura suave
                peak = int(h * (0.35 + 0.45 * abs((x / w) - 0.55)))
                if y < peak:
                    px[x, y] = (40 + int(120 * x / w), 90 + int(100 * y / h), 180 + int(60 * (1 - y / h)))
                else:
                    px[x, y] = (25 + int(50 * x / w), 75 + int(80 * (y - peak) / max(1, h - peak)), 35)
            else:
                # Patrón radicalmente opuesto (ajedrez diagonal + gradiente invertido)
                band = ((x // 24) + (y // 24)) % 2
                px[x, y] = (235 - int(80 * y / h), 55, 70) if band == 0 else (20, 25 + int(90 * x / w), 40)
    buf = io.BytesIO()
    img.save(buf, format=fmt, quality=85)
    return buf.getvalue()


def _recompress_resize(raw_bytes: bytes, size=(120, 90), quality: int = 70) -> bytes:
    """Simula la recompresión y redimensionamiento que aplican las redes sociales a una foto."""
    with Image.open(io.BytesIO(raw_bytes)) as img:
        resized = img.resize(size)
        buf = io.BytesIO()
        resized.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()


class TestVisionAnalysis(unittest.TestCase):
    def test_dhash_detects_recompressed_duplicate_and_separates_different(self):
        b1 = _make_sample_image("mountain", (240, 180), "PNG")
        b2 = _recompress_resize(b1, (120, 90), quality=70)
        b3 = _make_sample_image("bars", (200, 150), "PNG")

        h1 = compute_dhash_from_image(Image.open(io.BytesIO(b1)))
        h2 = compute_dhash_from_image(Image.open(io.BytesIO(b2)))
        h3 = compute_dhash_from_image(Image.open(io.BytesIO(b3)))

        self.assertEqual(len(h1), 16)
        self.assertLessEqual(hamming_distance(h1, h2), 4)
        self.assertGreater(hamming_distance(h1, h3), 12)

    def test_local_context_analysis(self):
        raw = _make_sample_image("mountain", (180, 120))
        res = analyze_image_bytes(
            raw,
            alt_text="Amanecer en la montaña haciendo senderismo",
            caption="Mi cámara lista para la ruta de ciclismo #naturaleza",
            filename="montana_bici.jpg",
            provider="local_context",
        )
        self.assertEqual(res["engine"], "local_context")
        self.assertEqual(len(res["dhash"]), 16)
        self.assertTrue(len(res["dominant_colors"]) >= 1)
        labels = {t["label"] for t in res["themes"]}
        self.assertIn("Naturaleza y Viajes", labels)
        self.assertIn("Deportes y Fitness", labels)

    @patch("modules.vision_analysis.requests.post")
    @patch("modules.vision_analysis._check_ollama")
    def test_ollama_vlm_mock(self, mock_check, mock_post):
        mock_check.return_value = {
            "available": True,
            "server_up": True,
            "url": "http://127.0.0.1:11434",
            "models": ["qwen2.5-vl:latest"],
            "selected_model": "qwen2.5-vl:latest",
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "response": json.dumps({
                "description": "Bicicleta de ruta frente a un café de montaña.",
                "scene": "exterior / montaña",
                "objects": ["bicicleta", "casco", "taza de café"],
                "themes": ["Deportes y Fitness", "Gastronomía y Café"],
                "ocr_text": "CAFE CUMBRE",
                "confidence": "ALTA",
            })
        }
        mock_post.return_value = mock_resp

        res = analyze_image_bytes(_make_sample_image("mountain"), provider="ollama")
        self.assertEqual(res["engine"], "ollama:qwen2.5-vl:latest")
        self.assertEqual(res["ocr_text"], "CAFE CUMBRE")
        self.assertEqual(res["confidence"], "ALTA")
        self.assertIn("bicicleta", res["objects"])
        labels = {t["label"] for t in res["themes"]}
        self.assertIn("Deportes y Fitness", labels)
        self.assertIn("Gastronomía y Café", labels)


class TestVisionStorageAndApi(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "phase3.db"
        storage.set_db_path(self.db_path)
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_gallery_persistence_and_duplicate_summary(self):
        detail = storage.create_case(title="Caso Visión F3", subject_name="Mateo")
        cid = detail["case"]["id"]

        b1 = _make_sample_image("mountain", (240, 180), "PNG")
        b2 = _recompress_resize(b1, (120, 90), quality=70)
        b3 = _make_sample_image("bars", (160, 120), "PNG")

        m1 = storage.add_case_gallery_image(cid, "ruta1.png", "", "/api/reports/g/ruta1.png", "g", sha256="aaa", alt_text="Senderismo en montaña")
        m2 = storage.add_case_gallery_image(cid, "ruta1_recomp.jpg", "", "/api/reports/g/ruta1_recomp.jpg", "g", sha256="bbb", alt_text="Paisaje de montaña")
        m3 = storage.add_case_gallery_image(cid, "setup_code.png", "", "/api/reports/g/setup_code.png", "g", sha256="ccc", alt_text="Laptop con código python")

        for item, raw in ((m1, b1), (m2, b2), (m3, b3)):
            vres = analyze_image_bytes(raw, alt_text=item["alt_text"], filename=item["filename"], provider="local_context")
            self.assertTrue(storage.update_media_vision(cid, item["id"], vres))

        summary = storage.get_case_vision_summary(cid)
        self.assertEqual(summary["stats"]["total_media"], 3)
        self.assertEqual(summary["stats"]["analyzed"], 3)
        self.assertEqual(summary["stats"]["similar_pairs"], 1)
        self.assertGreaterEqual(summary["stats"]["themes_detected"], 2)

        # La foto 1 y la foto 2 deben detectarse como par similar por dHash
        pair = summary["similar_pairs"][0]
        self.assertEqual({pair["filename_a"], pair["filename_b"]}, {"ruta1.png", "ruta1_recomp.jpg"})

        # Validar handlers FastAPI
        import web_app
        caps = vision_capabilities(probe_ollama=False)
        self.assertTrue(caps["dhash"])
        api_res = web_app.api_case_vision_summary(cid)
        self.assertEqual(api_res["stats"]["analyzed"], 3)


if __name__ == "__main__":
    unittest.main()
