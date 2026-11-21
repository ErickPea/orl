# -*- coding: utf-8 -*-
"""Pruebas para Fase 2: Alta de amigos por URL, estado pending_manual y borrado en cascada aislado."""
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


class TestFriendsByLink(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        storage.set_db_path(Path(self._tmp.name) / "friends_link.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_add_friend_by_url_and_login_wall_pending_manual(self):
        case_id = storage.create_case(title="Caso Amigos Link", subject_name="Erick")["case"]["id"]
        r_ok = web_app.api_case_add_friend(
            case_id,
            web_app.FriendAddRequest(url="https://www.instagram.com/laura_publica/", platform="instagram", label="Amiga"),
        )
        self.assertEqual(r_ok["friend"]["status"], "active")
        self.assertEqual(r_ok["friend"]["owner_type"], "friend")

        r_wall = web_app.api_case_add_friend(
            case_id,
            web_app.FriendAddRequest(url="https://www.instagram.com/accounts/login/?next=/privado/", platform="instagram"),
        )
        self.assertEqual(r_wall["friend"]["status"], "pending_manual")

    def test_isolated_ingest_investigate_and_cascade_delete_only_friend(self):
        case_id = storage.create_case(title="Caso Cascada Amigo", subject_name="Erick")["case"]["id"]
        subj_bundle = empty_bundle("instagram", "erick144", access_method="browser_supervised")
        subj_bundle["posts"].append({"external_id": "sp1", "author": "erick144", "content": "Post del sujeto"})
        storage.ingest_connector_bundle(case_id, subj_bundle)

        fr = web_app.api_case_add_friend(
            case_id,
            web_app.FriendAddRequest(url="https://www.instagram.com/carlos_amigo/", platform="instagram", label="Carlos"),
        )["friend"]
        fid = fr["id"]
        sna_after_add = storage.compute_and_save_case_sna(case_id, record_run=False)
        self.assertIn("carlos_amigo", [m.get("id") for m in sna_after_add.get("close_circle", [])])

        fr_bundle = empty_bundle("instagram", "carlos_amigo", access_method="browser_supervised")
        fr_bundle["profile"] = {"username": "carlos_amigo", "display_name": "Carlos", "bio": "Fotógrafo viajero"}
        fr_bundle["posts"].append({"external_id": "fp1", "author": "carlos_amigo", "content": "Viaje en Cartagena #viajes #playa"})
        fr_bundle["media_items"].append({
            "filename": "carlos_playa.jpg",
            "media_type": "image",
            "source_url": "https://instagram.com/cp.jpg",
            "alt_text": "palmeras y balcones coloniales en Cartagena",
            "post_external_id": "fp1",
            "published_at": "2026-11-12 14:00:00",
        })
        storage.ingest_friend_bundle(case_id, fid, fr_bundle)

        inv = web_app.api_case_friend_investigate(case_id, fid, web_app.FriendInvestigateRequest(compare_with_subject=False))
        self.assertEqual(inv["locations_inferred"], 1)
        self.assertFalse(inv["compared_with_subject"])

        detail = web_app.api_case_friend_detail(case_id, fid)
        self.assertEqual(len(detail["posts"]), 1)
        self.assertEqual(len(detail["location_history"]), 1)
        self.assertIn("#viajes", detail["public_summary"]["interests"])
        self.assertEqual(detail["public_summary"]["bio"], "Fotógrafo viajero")

        # Enlazar al SNA del sujeto exige confirm=true
        from fastapi import HTTPException
        with self.assertRaises(HTTPException) as ctx:
            web_app.api_case_friend_link_sna(case_id, fid, web_app.FriendLinkSnaRequest(confirm=False))
        self.assertEqual(ctx.exception.status_code, 403)

        linked = web_app.api_case_friend_link_sna(
            case_id, fid, web_app.FriendLinkSnaRequest(confirm=True, visual_proximity_score=56.0, matches_count=2)
        )
        self.assertEqual(linked["friend_handle"], "carlos_amigo")

        # Borrar amigo elimina SOLO su contenido, el del sujeto sigue intacto
        del_res = web_app.api_case_delete_friend(case_id, fid)
        self.assertTrue(del_res["deleted"])
        self.assertEqual(len(web_app.api_case_list_friends(case_id)["friends"]), 0)
        self.assertEqual(len(storage.get_case_content(case_id)["posts"]), 1)
        sna_after_del = storage.compute_and_save_case_sna(case_id, record_run=False)
        self.assertNotIn("carlos_amigo", [m.get("id") for m in sna_after_del.get("close_circle", [])])


if __name__ == "__main__":
    unittest.main()
