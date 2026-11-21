# -*- coding: utf-8 -*-
"""Pruebas unitarias e integración para la Fase 1 (SQLite, Casos y Resolución de Entidades)."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import storage  # noqa: E402
from modules.entity_resolution import evaluate_case_resolution  # noqa: E402


class TestEntityResolution(unittest.TestCase):
    def test_cross_link_avatar_bio_and_homonym_warning(self):
        identifiers = [
            {"kind": "username", "value": "laura_dev99"},
            {"kind": "full_name", "value": "Laura Gómez"},
            {"kind": "location", "value": "Bogotá, Colombia"},
            {"kind": "email", "value": "laura@miempresa.co"},
        ]
        profiles = [
            {
                "id": 1,
                "site": "GitHub",
                "url": "https://github.com/laura_dev99",
                "username": "laura_dev99",
                "display_name": "Laura Gómez",
                "bio": "Ingeniera de software en Bogotá. Python, seguridad y datos. https://laura-blog.miempresa.co",
                "location": "Bogotá, Colombia",
                "avatar_url": "https://cdn.example.com/avatar_laura.png",
                "website": "https://reddit.com/user/laura_dev99",
                "sources": "Maigret+Sherlock",
                "osint_confidence": "ALTA",
                "manual_status": "auto",
            },
            {
                "id": 2,
                "site": "Reddit",
                "url": "https://reddit.com/user/laura_dev99",
                "username": "laura_dev99",
                "display_name": "Laura Gómez",
                "bio": "Ingeniera de software interesada en Python, seguridad y datos.",
                "location": "Bogotá",
                "avatar_url": "https://cdn.example.com/avatar_laura.png",
                "website": "",
                "sources": "Maigret",
                "osint_confidence": "MEDIA",
                "manual_status": "auto",
            },
            {
                "id": 3,
                "site": "ForoGenérico",
                "url": "https://foro.example.org/u/laura",
                "username": "laura",
                "display_name": "",
                "bio": "",
                "location": "Madrid, España",
                "avatar_url": "",
                "website": "",
                "sources": "Sherlock",
                "osint_confidence": "BAJA",
                "manual_status": "auto",
            },
        ]

        res = evaluate_case_resolution(profiles, identifiers, subject_name="Laura Gómez")
        by_id = {p["profile_id"]: p for p in res["profiles"]}

        self.assertEqual(by_id[1]["level"], "VERIFICADO")
        self.assertGreaterEqual(by_id[1]["score"], 80)
        self.assertEqual(by_id[2]["level"], "VERIFICADO")
        self.assertEqual(by_id[3]["level"], "REVISAR")
        self.assertLess(by_id[3]["score"], 40)

        link_types = {lk["type"] for lk in res["cross_links"]}
        self.assertIn("cross_link", link_types)
        self.assertIn("avatar", link_types)
        self.assertIn("bio_sim", link_types)
        self.assertTrue(any("homónimo" in w.lower() for w in res["warnings"]))

    def test_manual_override(self):
        profiles = [
            {
                "id": 10,
                "site": "SitioA",
                "url": "https://a.example.com/u1",
                "username": "u1",
                "osint_confidence": "BAJA",
                "sources": "Sherlock",
                "manual_status": "confirmed",
            },
            {
                "id": 11,
                "site": "SitioB",
                "url": "https://b.example.com/u1",
                "username": "u1",
                "osint_confidence": "ALTA",
                "sources": "Maigret+Sherlock",
                "manual_status": "rejected",
            },
        ]
        res = evaluate_case_resolution(profiles, [], subject_name="")
        by_id = {p["profile_id"]: p for p in res["profiles"]}
        self.assertEqual(by_id[10]["level"], "VERIFICADO")
        self.assertGreaterEqual(by_id[10]["score"], 95)
        self.assertEqual(by_id[11]["level"], "DESCARTADO")
        self.assertEqual(by_id[11]["score"], 0)


class TestStorageAndApi(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test_cases.db"
        storage.set_db_path(self.db_path)
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_case_lifecycle_and_ingestion(self):
        detail = storage.create_case(
            title="Auditoría propia",
            subject_name="Carlos Ruiz",
            purpose="autodiagnostico",
            notes="Prueba Fase 1",
            seeds={"username": "carlosruiz_sec", "location": "Medellín, Colombia"},
        )
        cid = detail["case"]["id"]
        self.assertEqual(detail["case"]["title"], "Auditoría propia")
        self.assertGreaterEqual(len(detail["identifiers"]), 3)

        # Ingesta simulada de un job de tipo social
        ing = storage.ingest_job_result_to_case(
            case_id=cid,
            job_id="job123",
            kind="social",
            target="carlosruiz_sec",
            folder="2026-11-19_social_carlosruiz_sec",
            result={
                "kind": "social",
                "username": "carlosruiz_sec",
                "country": "co",
                "social_profiles": [
                    {
                        "sitio": "GitHub",
                        "url": "https://github.com/carlosruiz_sec",
                        "confianza": "ALTA",
                        "fuentes": "Maigret+Sherlock",
                        "public_meta": {
                            "name": "Carlos Ruiz",
                            "bio": "Seguridad defensiva en Medellín. https://reddit.com/user/carlosruiz_sec",
                            "location": "Medellín, Colombia",
                            "avatar": "https://avatars.example.com/cr.jpg",
                        },
                    },
                    {
                        "sitio": "Reddit",
                        "url": "https://reddit.com/user/carlosruiz_sec",
                        "confianza": "MEDIA",
                        "fuentes": "Maigret",
                        "public_meta": {
                            "name": "Carlos Ruiz",
                            "bio": "Seguridad defensiva y software libre.",
                            "location": "Medellín",
                            "avatar": "https://avatars.example.com/cr.jpg",
                        },
                    },
                ],
            },
        )
        self.assertEqual(ing["profiles_ingested"], 2)

        updated = storage.get_case_detail(cid)
        self.assertEqual(len(updated["profiles"]), 2)
        self.assertEqual(len(updated["runs"]), 1)
        self.assertEqual(updated["resolution"]["stats"]["verified"], 2)

        # Descartar manualmente uno y verificar recálculo
        pid = updated["profiles"][0]["id"]
        storage.set_profile_manual_status(cid, pid, "rejected")
        after_reject = storage.get_case_detail(cid)
        self.assertEqual(after_reject["resolution"]["stats"]["rejected"], 1)

        # Persistencia de job
        storage.save_job({
            "id": "job123",
            "case_id": cid,
            "kind": "social",
            "target": "carlosruiz_sec",
            "status": "done",
            "step": "Completado",
            "progress": 100,
            "folder": "2026-11-19_social_carlosruiz_sec",
            "started_at": "2026-11-19 16:00:00",
            "finished_at": "2026-11-19 16:00:10",
            "logs": ["ok"],
            "files": [],
            "result": {"kind": "social"},
        })
        persisted = storage.get_persisted_job("job123")
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted["case_id"], cid)

        # Eliminación en cascada
        del_res = storage.delete_case(cid, delete_files=False)
        self.assertTrue(del_res["deleted"])
        self.assertIsNone(storage.get_case_detail(cid))

    def test_fastapi_endpoints(self):
        import web_app

        # Status incluye cases_count y sqlite_cases
        st = web_app.api_status()
        self.assertTrue(st["capabilities"]["sqlite_cases"])
        self.assertEqual(st["cases_count"], 0)

        # Crear caso vía handler FastAPI
        created = web_app.api_cases_create(
            web_app.CaseCreateRequest(
                title="Caso API Test",
                subject_name="Ana Torres",
                purpose="auditoria",
                seed_username="anatorres_dev",
                seed_location="Cali, Colombia",
            )
        )
        cid = created["case"]["id"]

        # Añadir perfil vía handler FastAPI
        added = web_app.api_case_add_profile(
            cid,
            web_app.ProfileCreateRequest(
                site="GitHub",
                url="https://github.com/anatorres_dev",
                username="anatorres_dev",
                display_name="Ana Torres",
                location="Cali, Colombia",
                sources="Maigret+Sherlock",
                osint_confidence="ALTA",
            ),
        )
        detail = added["detail"]
        self.assertEqual(detail["resolution"]["stats"]["verified"], 1)

        # Confirmar / descartar perfil vía PATCH handler
        pid = detail["profiles"][0]["id"]
        patched = web_app.api_case_patch_profile(
            cid,
            pid,
            web_app.ProfileStatusPatchRequest(manual_status="rejected"),
        )
        self.assertEqual(patched["resolution"]["stats"]["rejected"], 1)

        # Listar y eliminar caso
        listed = web_app.api_cases_list()
        self.assertEqual(len(listed["cases"]), 1)

        deleted = web_app.api_case_delete(cid)
        self.assertTrue(deleted["deleted"])
        self.assertEqual(web_app.api_status()["cases_count"], 0)


if __name__ == "__main__":
    unittest.main()
