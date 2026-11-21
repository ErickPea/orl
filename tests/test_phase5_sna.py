# -*- coding: utf-8 -*-
"""Pruebas unitarias e integración para la Fase 5 (Grafo SNA, Círculo Cercano y Comunidades)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import storage  # noqa: E402
from modules.connectors import parse_imported_content  # noqa: E402
from modules.sna_graph import build_case_sna  # noqa: E402


class TestSnaGraph(unittest.TestCase):
    def test_reciprocity_tiers_communities_and_shared_themes(self):
        inputs = {
            "subject_name": "Laura Gómez",
            "subject_handles": ["laura_dev", "laura_bsky"],
            "interactions": [
                # Amigo íntimo (recíproco, fuerte, múltiples días)
                {"source_handle": "laura_dev", "target_handle": "carlos_bici", "kind": "reply", "weight": 6.0, "evidence_count": 4, "metadata": {"platform": "bluesky"}},
                {"source_handle": "carlos_bici", "target_handle": "laura_bsky", "kind": "comment", "weight": 6.4, "evidence_count": 4, "metadata": {"platform": "bluesky"}},
                # Segundo amigo del mismo grupo de ciclismo
                {"source_handle": "laura_dev", "target_handle": "diana_ruta", "kind": "comment", "weight": 3.2, "evidence_count": 2, "metadata": {"platform": "bluesky"}},
                {"source_handle": "diana_ruta", "target_handle": "laura_dev", "kind": "comment", "weight": 3.2, "evidence_count": 2, "metadata": {"platform": "bluesky"}},
                {"source_handle": "carlos_bici", "target_handle": "diana_ruta", "kind": "mention", "weight": 2.0, "evidence_count": 2, "metadata": {"platform": "bluesky"}},
                # Grupo de trabajo/código
                {"source_handle": "laura_dev", "target_handle": "mateo_py", "kind": "collab", "weight": 3.0, "evidence_count": 2, "metadata": {"platform": "github"}},
                {"source_handle": "mateo_py", "target_handle": "sofia_sec", "kind": "mention", "weight": 2.5, "evidence_count": 2, "metadata": {"platform": "github"}},
                {"source_handle": "laura_dev", "target_handle": "sofia_sec", "kind": "mention", "weight": 1.0, "evidence_count": 1, "metadata": {"platform": "github"}},
                # Cuenta periférica unidireccional
                {"source_handle": "desconocido_1", "target_handle": "laura_dev", "kind": "mention", "weight": 1.0, "evidence_count": 1, "metadata": {"platform": "reddit"}},
            ],
            "comments": [
                {"post_id": 1, "author_handle": "carlos_bici", "target_handle": "laura_dev", "content": "¡Excelente ruta de ciclismo hoy!", "sentiment": "POSITIVO", "published_at": "2026-10-01 09:00:00"},
                {"post_id": 1, "author_handle": "laura_dev", "target_handle": "carlos_bici", "content": "Genial entrenar bicicleta en la montaña contigo.", "sentiment": "POSITIVO", "published_at": "2026-10-03 10:00:00"},
                {"post_id": 1, "author_handle": "diana_ruta", "target_handle": "laura_dev", "content": "Me encanta el ciclismo los domingos!", "sentiment": "POSITIVO", "published_at": "2026-10-03 11:00:00"},
                {"post_id": 2, "author_handle": "mateo_py", "target_handle": "laura_dev", "content": "Revisando el código Python y ciberseguridad en GitHub.", "sentiment": "NEUTRO", "published_at": "2026-10-05 15:00:00"},
                {"post_id": 2, "author_handle": "sofia_sec", "target_handle": "laura_dev", "content": "Buen análisis de privacidad y software.", "sentiment": "POSITIVO", "published_at": "2026-10-05 16:00:00"},
            ],
        }

        sna = build_case_sna(inputs)
        circle = sna["close_circle"]
        by_id = {u["id"]: u for u in circle}

        # Carlos debe ser el #1 en el círculo íntimo y recíproco
        self.assertEqual(circle[0]["id"], "carlos_bici")
        self.assertEqual(by_id["carlos_bici"]["tier"], "INTIMO")
        self.assertTrue(by_id["carlos_bici"]["is_reciprocal"])
        self.assertGreater(by_id["carlos_bici"]["closeness_score"], by_id["desconocido_1"]["closeness_score"])
        self.assertEqual(by_id["carlos_bici"]["relationship_sentiment"]["dominant"], "POSITIVO")
        self.assertTrue(any(t["label"] == "Deportes y Fitness" for t in by_id["carlos_bici"]["shared_themes"]))
        self.assertEqual(len(by_id["carlos_bici"]["sample_comments"]), 2)

        # Las dos comunidades (ciclismo vs tecnología) deben separarse correctamente
        self.assertEqual(by_id["carlos_bici"]["community_id"], by_id["diana_ruta"]["community_id"])
        self.assertEqual(by_id["mateo_py"]["community_id"], by_id["sofia_sec"]["community_id"])
        self.assertNotEqual(by_id["carlos_bici"]["community_id"], by_id["mateo_py"]["community_id"])
        self.assertGreaterEqual(sna["stats"]["communities_count"], 2)


class TestSnaStorageAndApi(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "phase5.db"
        storage.set_db_path(self.db_path)
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_sna_end_to_end_and_fastapi_handlers(self):
        detail = storage.create_case(title="Caso SNA F5", subject_name="Andrés", seeds={"username": "andres_co"})
        cid = detail["case"]["id"]

        sample_json = {
            "platform": "demo",
            "subject_handle": "andres_co",
            "posts": [
                {"id": "p1", "author": "andres_co", "content": "Saliendo a correr con @camila_run #running", "published_at": "2026-10-10 07:00:00"}
            ],
            "comments": [
                {"id": "c1", "post_id": "p1", "author": "camila_run", "target": "andres_co", "content": "¡Excelente entrenamiento!", "published_at": "2026-10-10 07:30:00"},
                {"id": "c2", "post_id": "p1", "author": "andres_co", "target": "camila_run", "content": "¡Vamos por el maratón!", "published_at": "2026-10-11 08:00:00"},
            ],
        }
        bundle = parse_imported_content(json.dumps(sample_json).encode("utf-8"), "demo.json")
        storage.ingest_connector_bundle(cid, bundle)

        import web_app
        sna = web_app.api_case_sna(cid)
        self.assertEqual(sna["stats"]["alters_count"], 1)
        self.assertEqual(sna["close_circle"][0]["id"], "camila_run")
        self.assertTrue(sna["close_circle"][0]["is_reciprocal"])

        recomp = web_app.api_case_sna_recompute(cid)
        self.assertEqual(recomp["stats"]["reciprocal_count"], 1)
        # Verificar que `recompute` dejó bitácora en case_runs
        case_after = storage.get_case_detail(cid)
        self.assertTrue(any(r["kind"] == "sna:graph" for r in case_after["runs"]))

    def test_meta_export_whatsapp_and_manual_sna_interaction(self):
        import io
        import zipfile
        import web_app

        detail = storage.create_case(title="Caso Meta + WA", subject_name="Erick", seeds={"username": "erickpena144"})
        cid = detail["case"]["id"]

        # 1) Exportación ZIP estilo Meta (Facebook friends_v2 + Instagram comments string_map_data)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "friends/friends.json",
                json.dumps({"friends_v2": [{"name": "Sofía Gómez", "timestamp": 1760000000}]}),
            )
            zf.writestr(
                "comments/post_comments_1.json",
                json.dumps([
                    {
                        "string_map_data": {
                            "Comment": {"value": "Excelente partido de futbol!"},
                            "Media Owner": {"value": "juan_dev"},
                            "Time": {"timestamp": 1760000100},
                        }
                    }
                ]),
            )
        bundle_zip = parse_imported_content(buf.getvalue(), "facebook_export.zip", platform_label="facebook", subject_handle="erickpena144")
        storage.ingest_connector_bundle(cid, bundle_zip)

        # 2) Chat exportado de WhatsApp (.txt)
        wa_txt = (
            "15/10/2026, 09:10 - Maria Perez: Hola Erick, vamos al cafe hoy?\n"
            "15/10/2026, 09:12 - erickpena144: Si, excelente idea!\n"
        ).encode("utf-8")
        bundle_wa = parse_imported_content(wa_txt, "chat_maria.txt", platform_label="whatsapp", subject_handle="erickpena144")
        storage.ingest_connector_bundle(cid, bundle_wa)

        # 3) Registro manual directo en SNA
        sna_after = web_app.api_case_sna_add_interaction(
            cid,
            web_app.ManualInteractionRequest(
                peer_handle="amigo_threads",
                direction="mutual",
                kind="comment",
                platform="threads",
                content="Buen proyecto de programación Python!",
            ),
        )
        ids = {u["id"] for u in sna_after["close_circle"]}
        self.assertIn("juan_dev", ids)
        self.assertIn("maria_perez", ids)
        self.assertIn("amigo_threads", ids)
        self.assertGreaterEqual(sna_after["stats"]["alters_count"], 3)


if __name__ == "__main__":
    unittest.main()
