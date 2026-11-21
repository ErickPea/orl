# -*- coding: utf-8 -*-
"""Pruebas unitarias e integración para la Fase 6 (Reporte Integral, Exposición y Gobernanza)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import case_report, storage, utils  # noqa: E402
from modules.connectors import parse_imported_content, sample_import_template  # noqa: E402


class TestPhase6ReportAndGovernance(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.db_path = self.tmp_dir / "phase6.db"
        self.out_dir = self.tmp_dir / "output"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        storage.set_db_path(self.db_path)
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def _build_rich_case(self) -> str:
        detail = storage.create_case(
            title="Auditoría Integral 2026",
            subject_name="Mi Usuario",
            purpose="autodiagnostico",
            seeds={"username": "mi_usuario", "location": "Bogotá, Colombia"},
        )
        cid = detail["case"]["id"]
        bundle = parse_imported_content(json.dumps(sample_import_template()).encode("utf-8"), "export.json")
        storage.ingest_connector_bundle(cid, bundle)

        # Añadir segundo perfil y foto con GPS para disparar evaluación de exposición completa
        storage.upsert_profile(
            cid,
            site="GitHub",
            url="https://github.com/mi_usuario",
            username="mi_usuario",
            display_name="Mi Usuario",
            bio="Ingeniero de software en Bogotá. https://ejemplo.com/mi_usuario",
            location="Bogotá, Colombia",
            sources="Maigret+Sherlock",
            osint_confidence="ALTA",
            recalc=True,
        )
        storage.add_case_gallery_image(
            cid,
            filename="foto_gps.jpg",
            local_path="",
            public_url="/api/reports/g/foto_gps.jpg",
            folder="case_gallery_ref",
            sha256="gps123",
            gps_lat=4.711,
            gps_lon=-74.0721,
        )
        return cid

    def test_integral_report_with_anonymization_and_exposure(self):
        cid = self._build_rich_case()

        with patch.object(utils, "OUTPUT_ROOT", self.out_dir):
            res = case_report.generate_case_report(
                cid,
                anonymize=True,
                include_quotes=True,
                include_gallery=True,
            )
            self.assertIsNotNone(res)
            self.assertTrue(res["governance"]["anonymize_third_parties"])
            self.assertGreaterEqual(res["governance"]["anonymized_count"], 1)

            # Exposición debe detectar GPS, perfiles verificados y círculo recíproco
            exp = res["exposure"]
            self.assertGreaterEqual(exp["score"], 35)
            self.assertTrue(any("GPS" in f for f in exp["findings"]))
            self.assertTrue(len(exp["recommendations"]) >= 2)

            # Verificar los 3 archivos generados (HTML, PDF, JSON)
            folder = self.out_dir / res["folder"]
            html_file = folder / "resumen.html"
            json_file = folder / "caso_completo.json"
            pdf_file = folder / "reporte_final.pdf"
            self.assertTrue(html_file.is_file())
            self.assertTrue(json_file.is_file())
            self.assertTrue(pdf_file.is_file())
            self.assertGreater(pdf_file.stat().st_size, 500)

            # Verificar que el handle de tercero ("amigo_cercano") NO se filtró al HTML ni al JSON
            html_txt = html_file.read_text(encoding="utf-8")
            json_txt = json_file.read_text(encoding="utf-8")
            self.assertNotIn("amigo_cercano", html_txt)
            self.assertNotIn("amigo_cercano", json_txt)
            self.assertIn("Tercero_01", html_txt)
            self.assertIn("Tercero_01", json_txt)

    def test_storage_maintenance_and_orphan_cleanup(self):
        cid = self._build_rich_case()
        # Carpeta huérfana no vinculada a ningún caso
        orphan = self.out_dir / "2026-11-18_000000_old_orphan"
        orphan.mkdir(parents=True, exist_ok=True)
        (orphan / "temp.txt").write_text("datos temporales", encoding="utf-8")

        # Carpeta referenciada por el caso (NO debe borrarse)
        ref_dir = self.out_dir / "case_gallery_ref"
        ref_dir.mkdir(parents=True, exist_ok=True)
        (ref_dir / "keep.txt").write_text("protegido", encoding="utf-8")

        with patch.object(utils, "OUTPUT_ROOT", self.out_dir):
            import web_app

            st = web_app.api_maintenance_storage()
            self.assertEqual(st["orphan_folders_count"], 1)
            self.assertEqual(st["referenced_folders_count"], 1)

            dry = web_app.api_maintenance_cleanup(web_app.CleanupRequest(dry_run=True))
            self.assertEqual(dry["would_delete_count"], 1)
            self.assertTrue(orphan.exists())

            done = web_app.api_maintenance_cleanup(web_app.CleanupRequest(dry_run=False))
            self.assertEqual(done["deleted_count"], 1)
            self.assertFalse(orphan.exists())
            self.assertTrue(ref_dir.exists())

            # Probar handler de exposición directa
            exp = web_app.api_case_exposure(cid)
            self.assertIn(exp["level"], ("BAJA", "MODERADA", "ALTA", "CRÍTICA"))


if __name__ == "__main__":
    unittest.main()
