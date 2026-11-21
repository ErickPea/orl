# -*- coding: utf-8 -*-
"""Pruebas unitarias e integración para Investigación Autorizada de Cuentas, OAuth2 y Evidencias."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import case_report, platform_auth, storage, utils  # noqa: E402
from modules.account_investigator import investigate_social_account  # noqa: E402
from modules.connectors import platforms_capabilities_catalog  # noqa: E402
from modules.connectors.meta_graph import fetch_facebook_page, fetch_instagram_pro  # noqa: E402
from modules.connectors.reddit import fetch_reddit  # noqa: E402


def _mock_resp(payload, status_code=200):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = payload
    return m


class TestPlatformAuthAndCapabilities(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        storage.set_db_path(self.tmp_path / "auth.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_token_encryption_roundtrip_and_no_secret_leak_in_api(self):
        with patch.object(platform_auth, "KEY_FILE", self.tmp_path / ".token_key"):
            enc = platform_auth.encrypt_token_payload({"access_token": "tok_secreto_123", "refresh_token": "ref_456"})
            self.assertNotIn("tok_secreto_123", enc)
            dec = platform_auth.decrypt_token_payload(enc)
            self.assertEqual(dec["access_token"], "tok_secreto_123")
            # Tampering debe fallar la verificación HMAC
            self.assertIsNone(platform_auth.decrypt_token_payload(enc[:-4] + "AAAA"))

            saved = storage.save_platform_connection(
                platform="instagram_pro",
                account_label="@mi_negocio",
                external_account_id="17841400",
                auth_type="oauth2",
                scopes=["instagram_basic"],
                token_encrypted=enc,
            )
            self.assertNotIn("token_encrypted", saved)

            import web_app
            listed = web_app.api_auth_connections()
            self.assertEqual(len(listed["connections"]), 1)
            self.assertNotIn("token_encrypted", listed["connections"][0])

    def test_oauth_callback_rejects_invalid_csrf_state(self):
        with self.assertRaises(ValueError):
            platform_auth.complete_oauth_callback("reddit", code="abc", state="estado_falso")

    def test_capabilities_matrix_honestly_reports_meta_limits(self):
        caps = {c["id"]: c for c in platforms_capabilities_catalog()}
        self.assertTrue(caps["reddit"]["supports_third_party_public"])
        self.assertTrue(caps["bluesky"]["supports_reactions"])
        self.assertFalse(caps["instagram_pro"]["supports_third_party_public"])
        self.assertFalse(caps["facebook_page"]["supports_third_party_public"])


class TestMetaAndRedditConnectors(unittest.TestCase):
    @patch("modules.connectors.meta_graph.requests.get")
    def test_instagram_own_pro_account_extracts_comments_replies_and_reactions(self, mock_get):
        mock_get.side_effect = [
            _mock_resp({"data": [{"id": "pg1", "instagram_business_account": {"id": "ig1", "username": "mi_marca"}}]}),
            _mock_resp({"id": "ig1", "username": "mi_marca", "name": "Mi Marca", "biography": "Web https://mimarca.co", "followers_count": 500}),
            _mock_resp({
                "data": [
                    {
                        "id": "m1",
                        "caption": "Lanzamiento con @socio_dev #tech",
                        "media_type": "IMAGE",
                        "media_url": "https://cdn.ig/m1.jpg",
                        "permalink": "https://instagram.com/p/m1",
                        "timestamp": "2026-11-12T15:00:00Z",
                        "like_count": 42,
                        "comments_count": 1,
                    }
                ]
            }),
            _mock_resp({
                "data": [
                    {
                        "id": "cm1",
                        "text": "¡Felicidades mi hermano mayor, qué gran logro!",
                        "username": "carlos_fam",
                        "timestamp": "2026-11-12T15:20:00Z",
                        "replies": {
                            "data": [
                                {"id": "rep1", "text": "¡Gracias hermano!", "username": "mi_marca", "timestamp": "2026-11-12T15:25:00Z"}
                            ]
                        },
                    }
                ]
            }),
        ]
        b = fetch_instagram_pro("mi_marca", access_token="fake_tok")
        self.assertEqual(b["access_method"], "oauth2_authorized")
        self.assertEqual(len(b["posts"]), 1)
        self.assertEqual(len(b["comments"]), 2)
        self.assertEqual(len(b["reactions"]), 1)
        self.assertEqual(b["reactions"][0]["count"], 42)

    @patch("modules.connectors.meta_graph.requests.get")
    def test_facebook_page_extracts_location_comments_and_reactions(self, mock_get):
        mock_get.side_effect = [
            _mock_resp({"data": [{"id": "fp1", "name": "Tech CO", "username": "techco", "about": "Comunidad", "access_token": "pg_tok"}]}),
            _mock_resp({
                "data": [
                    {
                        "id": "p1",
                        "message": "Evento en vivo hoy!",
                        "created_time": "2026-11-14T18:00:00Z",
                        "permalink_url": "https://facebook.com/techco/posts/1",
                        "place": {"name": "Bogotá, Colombia"},
                        "reactions": {"summary": {"total_count": 28}},
                        "comments": {"data": [{"id": "fc1", "message": "Ahí nos vemos parcero!", "from": {"name": "Andres"}}]},
                    }
                ]
            }),
        ]
        b = fetch_facebook_page("techco", access_token="fake_tok")
        self.assertEqual(b["posts"][0]["published_location"], "Bogotá, Colombia")
        self.assertEqual(b["reactions"][0]["count"], 28)
        self.assertEqual(len(b["comments"]), 1)

    @patch("modules.connectors.reddit.requests.get")
    def test_reddit_with_oauth_and_third_party_thread_replies(self, mock_get):
        mock_get.side_effect = [
            _mock_resp({"data": {"name": "ana_r", "subreddit": {"title": "Ana", "public_description": "https://ana.dev"}}}),
            _mock_resp({
                "data": {
                    "children": [
                        {
                            "data": {
                                "id": "post1",
                                "title": "Mi viaje familiar",
                                "selftext": "Con mi madre en Cartagena",
                                "subreddit": "travel",
                                "permalink": "/r/travel/comments/post1/viaje/",
                                "score": 19,
                                "num_comments": 1,
                                "created_utc": 1760000000,
                            }
                        }
                    ]
                }
            }),
            _mock_resp({"data": {"children": []}}),
            _mock_resp([
                {},
                {
                    "data": {
                        "children": [
                            {
                                "data": {
                                    "id": "rpl1",
                                    "author": "hermana_sofia",
                                    "body": "Qué lindas fotos mi hermana querida!",
                                    "permalink": "/r/travel/comments/post1/viaje/rpl1/",
                                    "created_utc": 1760000500,
                                }
                            }
                        ]
                    }
                },
            ]),
        ]
        b = fetch_reddit("ana_r", include_replies=True, include_reactions=True, fetch_post_replies=True, access_token="rd_tok")
        self.assertEqual(b["access_method"], "oauth2_authorized")
        self.assertEqual(len(b["reactions"]), 1)
        self.assertEqual(len(b["comments"]), 1)
        self.assertEqual(b["comments"][0]["author_handle"], "hermana_sofia")


class TestAccountInvestigatorEndToEnd(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.out_dir = self.tmp_dir / "output"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        storage.set_db_path(self.tmp_dir / "inv.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    @patch("modules.account_investigator.fetch_from_connector")
    def test_full_investigation_creates_evidences_relational_sna_and_anonymizes_report(self, mock_fetch):
        mock_fetch.return_value = {
            "platform": "bluesky",
            "handle": "erick.bsky.social",
            "access_method": "public_api",
            "profile_links": ["https://erick.dev"],
            "profile": {
                "site": "Bluesky",
                "url": "https://bsky.app/profile/erick.bsky.social",
                "username": "erick.bsky.social",
                "display_name": "Erick",
                "bio": "Software y montaña https://erick.dev",
                "location": "Bogotá",
                "avatar_url": "",
                "website": "https://erick.dev",
                "sources": "Bluesky-API",
                "osint_confidence": "ALTA",
            },
            "posts": [
                {
                    "external_id": "at://1",
                    "platform": "bluesky",
                    "author": "erick.bsky.social",
                    "content": "Aniversario con mi esposa @laura_real en Bogotá #viajes",
                    "published_at": "2026-11-15T12:00:00Z",
                    "url": "https://bsky.app/profile/erick.bsky.social/post/1",
                    "published_location": "Bogotá",
                    "media_urls": [],
                    "mentions": ["laura_real"],
                    "hashtags": ["viajes"],
                }
            ],
            "comments": [
                {
                    "external_id": "at://c1",
                    "post_external_id": "at://1",
                    "author_handle": "laura_real",
                    "target_handle": "erick.bsky.social",
                    "content": "Feliz aniversario mi esposo amado!",
                    "published_at": "2026-11-15T12:10:00Z",
                    "metadata": {"platform": "bluesky", "url": "https://bsky.app/profile/laura_real/post/c1"},
                }
            ],
            "media_items": [],
            "reactions": [{"post_external_id": "at://1", "platform": "bluesky", "reaction_type": "like", "actor_handle": "laura_real", "count": 1}],
            "interactions": [
                {"source_handle": "erick.bsky.social", "target_handle": "laura_real", "kind": "mention", "weight": 1.0, "evidence_count": 1},
                {"source_handle": "laura_real", "target_handle": "erick.bsky.social", "kind": "comment", "weight": 1.6, "evidence_count": 1},
            ],
            "warnings": [],
        }

        case = storage.create_case(title="Investigación Cuenta", subject_name="Erick")
        cid = case["case"]["id"]

        res = investigate_social_account(
            case_id=cid,
            platform="bluesky",
            handle="erick.bsky.social",
            run_relational=True,
            run_nlp=True,
            run_sna=True,
        )
        self.assertGreaterEqual(res["ingest"]["evidences_inserted"], 3)
        self.assertGreaterEqual(res["relational"]["explicit"], 1)

        evs = storage.get_case_evidences(cid)
        self.assertEqual(len(evs["profile_snapshots"]), 1)
        self.assertEqual(len(evs["reactions"]), 1)
        self.assertGreaterEqual(len(evs["evidences"]), 3)

        # Reporte con anonimización no debe filtrar `laura_real` ni siquiera dentro de citas relacionales
        with patch.object(utils, "OUTPUT_ROOT", self.out_dir):
            rep = case_report.generate_case_report(cid, anonymize=True)
            json_txt = (self.out_dir / rep["folder"] / "caso_completo.json").read_text(encoding="utf-8")
            self.assertNotIn("laura_real", json_txt)
            self.assertIn("Tercero_01", json_txt)


if __name__ == "__main__":
    unittest.main()
