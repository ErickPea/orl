# -*- coding: utf-8 -*-
"""Pruebas unitarias e integración para el Agente de Navegación Supervisada (Playwright)."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import browser_agent, storage  # noqa: E402
from modules.browser_adapters.base import BaseBrowserAdapter  # noqa: E402


class TestBrowserAgentControlAndGates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        storage.set_db_path(Path(self._tmp.name) / "agent.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_security_gate_detection_never_bypasses(self):
        adapter = BaseBrowserAdapter()
        page = MagicMock()
        page.url = "https://www.instagram.com/accounts/login/"
        gate = adapter.detect_security_or_login_gate(page)
        self.assertIn("login", gate.lower())

        page.url = "https://www.facebook.com/user"
        page.locator.return_value.count.return_value = 1
        gate_cap = adapter.detect_security_or_login_gate(page)
        self.assertIn("CAPTCHA", gate_cap)

    def test_pause_resume_cancel_state_machine(self):
        c = storage.create_case(title="Caso Agente", subject_name="Erick")
        cid = c["case"]["id"]
        run = storage.create_browser_agent_run(cid, "instagram", "https://instagram.com/erick")
        rid = run["id"]

        ctrl = browser_agent.RunControl(rid)
        ctrl.pause("Pausado por prueba", checkpoint=True)
        r1 = storage.get_browser_agent_run(rid)
        self.assertEqual(r1["status"], "paused_checkpoint")

        ctrl.resume()
        r2 = storage.get_browser_agent_run(rid)
        self.assertEqual(r2["status"], "running")

        ctrl.cancel()
        r3 = storage.get_browser_agent_run(rid)
        self.assertEqual(r3["status"], "cancelled")
        self.assertFalse(ctrl.wait_if_paused())


class TestStagingReviewAndCommit(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        storage.set_db_path(self.tmp_dir / "staging.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_staged_items_filter_delete_and_commit_to_case(self):
        c = storage.create_case(title="Caso Staging", subject_name="Laura")
        cid = c["case"]["id"]
        run = storage.create_browser_agent_run(cid, "instagram", "https://www.instagram.com/laura_real/")
        rid = run["id"]

        i_prof = storage.add_browser_staged_item(
            rid, cid, "profile", "instagram",
            author_handle="laura_real",
            content="Fotógrafa en Bogotá",
            source_url="https://www.instagram.com/laura_real/",
            payload={"site": "Instagram", "username": "laura_real", "display_name": "Laura"},
        )
        i_post = storage.add_browser_staged_item(
            rid, cid, "post", "instagram",
            author_handle="laura_real",
            content="Atardecer en la montaña #fotografia",
            published_at="2026-11-18T17:00:00Z",
            source_url="https://www.instagram.com/p/abc123/",
            payload={"external_id": "abc123"},
        )
        i_com_keep = storage.add_browser_staged_item(
            rid, cid, "comment", "instagram",
            author_handle="carlos_fam",
            target_handle="laura_real",
            content="Feliz cumpleaños mi hermana mayor!",
            source_url="https://www.instagram.com/p/abc123/",
            payload={"external_id": "c1", "post_external_id": "abc123"},
        )
        i_com_spam = storage.add_browser_staged_item(
            rid, cid, "comment", "instagram",
            author_handle="bot_spam",
            target_handle="laura_real",
            content="Gana dinero rápido aquí",
            source_url="https://www.instagram.com/p/abc123/",
            payload={"external_id": "c2", "post_external_id": "abc123"},
        )

        # Eliminar spam antes de consolidar
        import web_app
        self.assertTrue(web_app.api_agent_delete_item(rid, i_com_spam["id"])["deleted"])

        # Confirmar y consolidar solo los seleccionados
        res = web_app.api_agent_commit_run(rid, web_app.AgentCommitRequest(run_relational=True, run_nlp=True, run_sna=True))
        self.assertEqual(res["committed_items"], 3)
        self.assertEqual(res["ingest"]["posts_inserted"], 1)
        self.assertEqual(res["ingest"]["comments_inserted"], 1)

        # Verificar que el spam NO entró al caso y el comentario válido sí alimentó Relacional + SNA
        content = storage.get_case_content(cid)
        self.assertEqual(content["counts"]["comments"], 1)
        self.assertEqual(content["comments"][0]["author_handle"], "carlos_fam")

        rel = storage.list_relational_signals(cid)
        self.assertGreaterEqual(rel["stats"]["explicit"], 1)

        sna = storage.compute_and_save_case_sna(cid)
        self.assertEqual(sna["close_circle"][0]["id"], "carlos_fam")

    def test_sessions_endpoint_and_clear(self):
        with patch.object(browser_agent, "BROWSER_PROFILES_DIR", self.tmp_dir / "profiles"):
            p_ig = browser_agent.get_profile_dir("instagram")
            (p_ig / "Cookies").write_text("demo", encoding="utf-8")

            import web_app
            st = web_app.api_agent_sessions()
            self.assertTrue(st["playwright_ready"])
            ig_st = [s for s in st["sessions"] if s["platform"] == "instagram"][0]
            self.assertTrue(ig_st["has_saved_session"])

            clr = web_app.api_agent_clear_session("instagram")
            self.assertTrue(clr["cleared"])


if __name__ == "__main__":
    unittest.main()
