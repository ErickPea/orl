# -*- coding: utf-8 -*-
"""Pruebas para limpieza selectiva de cachés de navegador sin borrar cookies ni sesiones."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import web_app  # noqa: E402
from modules import browser_agent  # noqa: E402


class TestBrowserCacheCleanup(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.profiles_dir = Path(self._tmp.name) / "browser_profiles"

        fb = self.profiles_dir / "facebook"
        # Cachés pesados eliminables
        for rel in (
            "Default/Cache/Cache_Data/data_3",
            "Default/Code Cache/js/0af04481",
            "Default/Service Worker/ScriptCache/08b99d49",
            "Default/Service Worker/CacheStorage/blob_1",
            "GrShaderCache/data_3",
            "Edge Entity Extraction/2026.10/asset",
            "component_crx_cache/crx_1",
        ):
            p = fb / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"X" * 2048)

        # Archivos críticos de sesión/cookies que NO deben eliminarse nunca
        for rel in (
            "Local State",
            "Default/Preferences",
            "Default/Network/Cookies",
            "Default/Local Storage/leveldb/000003.log",
            "Default/IndexedDB/chrome.indexeddb/001.ldb",
            "Default/Service Worker/Database/CURRENT",
        ):
            p = fb / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"SESSION_KEEP")

    def tearDown(self):
        self._tmp.cleanup()

    def test_selective_cache_cleanup_preserves_cookies_and_sessions(self):
        stats = browser_agent.get_browser_cache_stats(platform="facebook", profiles_dir=self.profiles_dir)
        self.assertGreater(stats["cache_bytes"], 0)
        self.assertEqual(stats["platforms"]["facebook"]["targets_count"], 7)

        dry = browser_agent.clean_browser_cache(platform="facebook", dry_run=True, profiles_dir=self.profiles_dir)
        self.assertTrue(dry["dry_run"])
        self.assertEqual(dry["would_delete_count"], 7)
        self.assertTrue((self.profiles_dir / "facebook" / "Default" / "Code Cache" / "js" / "0af04481").exists())

        res = browser_agent.clean_browser_cache(platform="facebook", dry_run=False, profiles_dir=self.profiles_dir)
        self.assertFalse(res["dry_run"])
        self.assertEqual(res["deleted_count"], 7)
        self.assertTrue(res["sessions_preserved"])

        # Cachés borrados
        self.assertFalse((self.profiles_dir / "facebook" / "Default" / "Code Cache").exists())
        self.assertFalse((self.profiles_dir / "facebook" / "Default" / "Service Worker" / "ScriptCache").exists())
        self.assertFalse((self.profiles_dir / "facebook" / "Edge Entity Extraction").exists())

        # Cookies, Local Storage, IndexedDB y Service Worker/Database intactos
        for keep in (
            "Local State",
            "Default/Preferences",
            "Default/Network/Cookies",
            "Default/Local Storage/leveldb/000003.log",
            "Default/IndexedDB/chrome.indexeddb/001.ldb",
            "Default/Service Worker/Database/CURRENT",
        ):
            self.assertTrue((self.profiles_dir / "facebook" / keep).is_file(), f"Faltó preservar {keep}")

    def test_api_agent_clean_cache_endpoint(self):
        with patch.object(browser_agent, "BROWSER_PROFILES_DIR", self.profiles_dir):
            out = web_app.api_agent_clean_cache(web_app.BrowserCacheCleanRequest(platform="facebook", dry_run=False))
            self.assertEqual(out["deleted_count"], 7)
            st = browser_agent.session_status("facebook")
            self.assertTrue(st["has_saved_session"])
            self.assertEqual(st["cache_size_mb"], 0.0)


if __name__ == "__main__":
    unittest.main()
