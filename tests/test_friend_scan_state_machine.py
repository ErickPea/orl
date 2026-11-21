# -*- coding: utf-8 -*-
"""Pruebas para Módulo B: Cola FIFO de cuentas amigas, pausa por checkpoint y retorno al perfil principal."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from fastapi import HTTPException

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import browser_agent, storage  # noqa: E402
from modules.browser_adapters.base import BaseBrowserAdapter  # noqa: E402


class TestFriendScanStateMachine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        storage.set_db_path(Path(self._tmp.name) / "fscan.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_friend_scan_requires_explicit_opt_in_authorization(self):
        import web_app
        case_id = storage.create_case(title="Caso OptIn", subject_name="Sujeto")["case"]["id"]
        with self.assertRaises(HTTPException) as ctx:
            web_app.api_case_friend_scan_start(case_id, web_app.FriendScanStartRequest(authorized=False))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_fifo_queue_max_limit_and_return_to_primary_resume(self):
        case_id = storage.create_case(title="Caso Cola", subject_name="Sujeto")["case"]["id"]
        parent = storage.create_browser_agent_run(case_id, "instagram", "https://www.instagram.com/sujeto/")
        storage.update_browser_agent_run(parent["id"], resume_cursor="https://www.instagram.com/p/last_post/")

        candidates = [
            {
                "id": storage.upsert_interactant(case_id, "instagram", f"amigo_{i}", f"Amigo {i}", f"https://www.instagram.com/amigo_{i}/"),
                "handle": f"amigo_{i}",
                "platform": "instagram",
                "profile_url": f"https://www.instagram.com/amigo_{i}/",
            }
            for i in range(1, 6)
        ]
        fsm = browser_agent.FriendScanStateMachine(case_id=case_id, parent_run_id=parent["id"], platform="instagram")
        fsm.resume_cursor = "https://www.instagram.com/p/last_post/"
        queue = fsm.build_queue(candidates, max_profiles=3)

        self.assertEqual(fsm.state, "QUEUING_FRIENDS")
        self.assertEqual(len(queue), 3)
        self.assertEqual([q["target_handle"] for q in queue], ["amigo_1", "amigo_2", "amigo_3"])

        res = fsm.mark_primary_resume()
        self.assertEqual(res["state"], "DONE")
        self.assertEqual(res["resume_cursor"], "https://www.instagram.com/p/last_post/")
        fresh_parent = storage.get_browser_agent_run(parent["id"])
        self.assertEqual(fresh_parent["resume_cursor"], "https://www.instagram.com/p/last_post/")

    def test_paused_checkpoint_stops_on_captcha_without_evasion(self):
        case_id = storage.create_case(title="Caso Gate", subject_name="Sujeto")["case"]["id"]
        fsm = browser_agent.FriendScanStateMachine(case_id=case_id, platform="instagram")
        adapter = BaseBrowserAdapter()
        page = MagicMock()
        page.url = "https://www.instagram.com/challenge/"
        reason = adapter.detect_security_or_login_gate(page)
        self.assertTrue(bool(reason))

        fsm.pause(checkpoint=True)
        self.assertEqual(fsm.state, "PAUSED_CHECKPOINT")
        fsm.resume()
        self.assertEqual(fsm.state, "RESUMING")
        fsm.cancel()
        self.assertEqual(fsm.state, "CANCELLED")


if __name__ == "__main__":
    unittest.main()
