# -*- coding: utf-8 -*-
"""Pruebas unitarias e integración para la Fase 2 (Conectores APIs Abiertas + Importador)."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import storage  # noqa: E402
from modules.connectors import (  # noqa: E402
    connectors_catalog,
    fetch_from_connector,
    parse_imported_content,
    sample_import_template,
)


def _mock_resp(payload, status_code=200):
    m = MagicMock()
    m.status_code = status_code
    m.json.return_value = payload
    return m


class TestConnectors(unittest.TestCase):
    @patch("modules.connectors.bluesky.requests.get")
    def test_bluesky_connector(self, mock_get):
        mock_get.side_effect = [
            _mock_resp({
                "did": "did:plc:abc123",
                "handle": "ana.bsky.social",
                "displayName": "Ana Dev",
                "description": "Fotografía y Python #dev",
                "avatar": "https://cdn.bsky.app/avatar.jpg",
                "followersCount": 120,
                "followsCount": 80,
                "postsCount": 42,
            }),
            _mock_resp({
                "feed": [
                    {
                        "post": {
                            "uri": "at://did:plc:abc123/app.bsky.feed.post/3kxyz",
                            "author": {"handle": "ana.bsky.social"},
                            "record": {
                                "text": "Probando cámara nueva con @carlos.bsky.social #fotografia",
                                "createdAt": "2026-10-10T14:00:00Z",
                            },
                            "embed": {
                                "images": [
                                    {
                                        "fullsize": "https://cdn.bsky.app/img1.jpg",
                                        "thumb": "https://cdn.bsky.app/img1_t.jpg",
                                        "alt": "Paisaje de montaña al amanecer",
                                    }
                                ]
                            },
                            "likeCount": 15,
                            "replyCount": 1,
                        }
                    }
                ]
            }),
            _mock_resp({
                "thread": {
                    "replies": [
                        {
                            "post": {
                                "uri": "at://did:plc:xyz/app.bsky.feed.post/reply1",
                                "author": {"handle": "carlos.bsky.social"},
                                "record": {
                                    "text": "¡Qué fotaza!",
                                    "createdAt": "2026-10-10T14:15:00Z",
                                },
                            }
                        }
                    ]
                }
            }),
        ]

        bundle = fetch_from_connector("bluesky", "ana", limit=10, include_replies=True)
        self.assertEqual(bundle["profile"]["site"], "Bluesky")
        self.assertEqual(bundle["profile"]["username"], "ana.bsky.social")
        self.assertEqual(len(bundle["posts"]), 1)
        self.assertEqual(len(bundle["media_items"]), 1)
        self.assertEqual(bundle["media_items"][0]["alt_text"], "Paisaje de montaña al amanecer")
        self.assertEqual(len(bundle["comments"]), 1)
        self.assertEqual(bundle["comments"][0]["author_handle"], "carlos.bsky.social")
        self.assertGreaterEqual(len(bundle["interactions"]), 2)

    @patch("modules.connectors.mastodon.requests.get")
    def test_mastodon_connector(self, mock_get):
        mock_get.side_effect = [
            _mock_resp({
                "id": "999",
                "username": "luis",
                "display_name": "Luis M",
                "url": "https://mastodon.social/@luis",
                "note": "<p>Amante del ciclismo y Linux</p>",
                "avatar_static": "https://files.mastodon.social/av.png",
                "fields": [{"name": "Web", "value": '<a href="https://luis.dev">https://luis.dev</a>'}],
            }),
            _mock_resp([
                {
                    "id": "555",
                    "created_at": "2026-10-12T08:00:00Z",
                    "url": "https://mastodon.social/@luis/555",
                    "content": "<p>Ruta matutina #ciclismo con @pedro@fosstodon.org</p>",
                    "account": {"acct": "luis@mastodon.social"},
                    "mentions": [{"acct": "pedro@fosstodon.org"}],
                    "tags": [{"name": "ciclismo"}],
                    "media_attachments": [
                        {"type": "image", "url": "https://files.mastodon.social/bici.jpg", "description": "Bicicleta en ruta"}
                    ],
                    "replies_count": 1,
                }
            ]),
            _mock_resp({
                "descendants": [
                    {
                        "id": "556",
                        "created_at": "2026-10-12T08:20:00Z",
                        "content": "<p>¡Excelente subida!</p>",
                        "account": {"acct": "pedro@fosstodon.org"},
                    }
                ]
            }),
        ]

        bundle = fetch_from_connector("mastodon", "@luis@mastodon.social", limit=10, include_replies=True)
        self.assertEqual(bundle["profile"]["bio"], "Amante del ciclismo y Linux")
        self.assertEqual(bundle["profile"]["website"], "https://luis.dev")
        self.assertEqual(len(bundle["posts"]), 1)
        self.assertEqual(len(bundle["media_items"]), 1)
        self.assertEqual(len(bundle["comments"]), 1)

    @patch("modules.connectors.reddit.requests.get")
    def test_reddit_connector(self, mock_get):
        mock_get.side_effect = [
            _mock_resp({
                "data": {
                    "name": "sara_tech",
                    "total_karma": 500,
                    "subreddit": {"title": "Sara", "public_description": "Hardware y café"},
                    "icon_img": "https://styles.redditmedia.com/sara.png?width=256",
                }
            }),
            _mock_resp({
                "data": {
                    "children": [
                        {
                            "data": {
                                "id": "p1",
                                "title": "Mi teclado mecánico custom",
                                "selftext": "Hecho con ayuda de u/amigo_kb",
                                "subreddit": "MechanicalKeyboards",
                                "permalink": "/r/MechanicalKeyboards/comments/p1/teclado/",
                                "url_overridden_by_dest": "https://i.redd.it/kb1.jpg",
                                "created_utc": 1760000000,
                            }
                        }
                    ]
                }
            }),
            _mock_resp({
                "data": {
                    "children": [
                        {
                            "data": {
                                "id": "c1",
                                "body": "Gracias por la guía!",
                                "subreddit": "python",
                                "link_author": "mentor_py",
                                "link_id": "t3_xyz",
                                "created_utc": 1760001000,
                            }
                        }
                    ]
                }
            }),
        ]

        bundle = fetch_from_connector("reddit", "u/sara_tech", limit=10, include_replies=True)
        self.assertEqual(bundle["profile"]["site"], "Reddit")
        self.assertEqual(len(bundle["posts"]), 1)
        self.assertEqual(len(bundle["media_items"]), 1)
        self.assertEqual(len(bundle["comments"]), 1)
        self.assertTrue(any(ix["target_handle"] == "mentor_py" for ix in bundle["interactions"]))

    @patch("modules.connectors.github.requests.get")
    def test_github_connector(self, mock_get):
        mock_get.side_effect = [
            _mock_resp({
                "login": "devco",
                "name": "Dev Colombia",
                "bio": "Seguridad y Python",
                "location": "Bogotá",
                "html_url": "https://github.com/devco",
                "avatar_url": "https://avatars.githubusercontent.com/u/1",
                "blog": "https://devco.org",
            }),
            _mock_resp([
                {
                    "id": 101,
                    "name": "osint-tools",
                    "description": "Utilidades de análisis público",
                    "language": "Python",
                    "topics": ["osint", "security"],
                    "html_url": "https://github.com/devco/osint-tools",
                    "stargazers_count": 25,
                }
            ]),
            _mock_resp([
                {
                    "id": "ev1",
                    "type": "IssueCommentEvent",
                    "repo": {"name": "partner_org/shared-lib"},
                    "created_at": "2026-10-14T10:00:00Z",
                    "payload": {
                        "comment": {
                            "id": 88,
                            "body": "Buen PR @partner_dev, aprobado.",
                            "html_url": "https://github.com/partner_org/shared-lib/issues/1#issuecomment-88",
                        }
                    },
                }
            ]),
        ]

        bundle = fetch_from_connector("github", "devco", limit=10, include_replies=True)
        self.assertEqual(bundle["profile"]["location"], "Bogotá")
        self.assertEqual(len(bundle["posts"]), 1)
        self.assertEqual(len(bundle["comments"]), 1)
        self.assertTrue(any(ix["target_handle"] == "partner_dev" for ix in bundle["interactions"]))


class TestImporterAndStorage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "phase2.db"
        storage.set_db_path(self.db_path)
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_json_template_import_and_deduplication(self):
        detail = storage.create_case(title="Caso Fase 2", subject_name="Mi Usuario")
        cid = detail["case"]["id"]

        raw_json = json.dumps(sample_import_template()).encode("utf-8")
        bundle = parse_imported_content(raw_json, "export.json")
        self.assertEqual(len(bundle["posts"]), 1)
        self.assertEqual(len(bundle["comments"]), 2)
        self.assertEqual(len(bundle["media_items"]), 1)

        # Primera ingesta
        s1 = storage.ingest_connector_bundle(cid, bundle)
        self.assertEqual(s1["posts_inserted"], 1)
        self.assertEqual(s1["comments_inserted"], 2)
        self.assertEqual(s1["media_inserted"], 1)

        # Segunda ingesta del mismo archivo: debe deduplicar (0 duplicados nuevos)
        s2 = storage.ingest_connector_bundle(cid, bundle)
        self.assertEqual(s2["posts_inserted"], 0)
        self.assertEqual(s2["comments_inserted"], 0)
        self.assertEqual(s2["media_inserted"], 0)

        content = storage.get_case_content(cid)
        self.assertEqual(content["counts"]["posts"], 1)
        self.assertEqual(content["counts"]["comments"], 2)
        self.assertEqual(content["counts"]["media"], 1)
        self.assertGreaterEqual(content["counts"]["interactions"], 2)
        self.assertTrue(any(t["tag"] == "senderismo" for t in content["top_hashtags"]))

    def test_csv_import(self):
        csv_data = (
            "type,id,author,target,content,date,media_url\n"
            "post,p1,maria,,Amo el café colombiano #cafe @juan,2026-10-01,https://example.com/cafe.jpg\n"
            "comment,c1,juan,maria,El mejor café del mundo!,2026-10-02,\n"
        ).encode("utf-8")
        bundle = parse_imported_content(csv_data, "datos.csv", platform_label="dataset_csv", subject_handle="maria")
        self.assertEqual(len(bundle["posts"]), 1)
        self.assertEqual(len(bundle["comments"]), 1)
        self.assertEqual(len(bundle["media_items"]), 1)
        self.assertEqual(len(bundle["interactions"]), 2)

    def test_phase2_api_handlers(self):
        import web_app

        cat = web_app.api_connectors_list()
        ids = {c["id"] for c in cat["connectors"]}
        self.assertEqual(ids, {"bluesky", "mastodon", "reddit", "github", "youtube"})
        self.assertEqual(len(connectors_catalog()), 5)

        tpl = web_app.api_ingest_template()
        self.assertIn("posts", tpl)
        self.assertIn("comments", tpl)

        c = storage.create_case(title="Caso API F2")
        cid = c["case"]["id"]
        content = web_app.api_case_content(cid)
        self.assertEqual(content["counts"]["posts"], 0)


if __name__ == "__main__":
    unittest.main()
