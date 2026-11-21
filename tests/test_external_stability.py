# -*- coding: utf-8 -*-
"""Pruebas de estabilidad externa: perfiles de selectores + telemetría, caché HTTP TTL/Retry-After y UI offline."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules import breach_check, http_client, storage  # noqa: E402
from modules.browser_adapters.facebook import FacebookBrowserAdapter  # noqa: E402
from modules.browser_adapters.selectors import get_platform_selectors  # noqa: E402


class TestExternalStability(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        storage.set_db_path(Path(self._tmp.name) / "ext_stab.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_declarative_selectors_override_and_fallback_telemetry(self):
        fb_default = get_platform_selectors("facebook")
        ig_default = get_platform_selectors("instagram")
        self.assertIn("comment_button", fb_default)
        self.assertIn("caption_strategies", ig_default)

        ov_path = Path(self._tmp.name) / "override.json"
        ov_path.write_text(
            json.dumps({"facebook": {"comment_button": {"min_width": 24}}}),
            encoding="utf-8",
        )
        fb_merged = get_platform_selectors("facebook", override_path=ov_path)
        self.assertEqual(fb_merged["comment_button"]["min_width"], 24)
        self.assertIn("strategies", fb_merged["comment_button"])

        # Simular page.screenshot y callback update_status
        class FakePage:
            def screenshot(self, path, full_page=False):
                Path(path).write_bytes(b"\x89PNG\r\n\x1a\nFAKE")

        logs = []
        adapter = FacebookBrowserAdapter()
        shot = adapter.record_selector_telemetry(
            FakePage(),
            lambda step, pct, log: logs.append(log),
            step_name="comment_button_post_1",
            failed_selector="fb_data_ad_comment_button",
            fallback_used="fb_aria_comment_with_icon",
            diag_dir=Path(self._tmp.name) / "diag",
        )
        self.assertTrue(shot and Path(shot).is_file())
        self.assertEqual(len(logs), 1)
        self.assertIn("fb_data_ad_comment_button", logs[0])
        self.assertIn("fb_aria_comment_with_icon", logs[0])

    def test_http_cache_ttl_and_retry_after_backoff(self):
        calls = []
        waits = []

        def fake_get(url, headers=None, params=None, timeout=12):
            calls.append(url)
            if len(calls) == 1:
                return SimpleNamespace(status_code=429, headers={"Retry-After": "2"}, json=lambda: {})
            return SimpleNamespace(
                status_code=200,
                headers={},
                json=lambda: {
                    "ExposedBreaches": {
                        "breaches_details": [
                            {"breach": "TestLeak", "domain": "example.com", "xposed_date": "2024", "xposed_data": "Emails;Passwords"}
                        ]
                    }
                },
            )

        res1 = http_client.cached_get_json(
            source="xposedornot",
            url="https://api.xposedornot.com/v1/breach-analytics?email=a@b.com",
            query_key="a@b.com",
            ttl_seconds=3600,
            max_retries=2,
            requester=fake_get,
            sleep_fn=lambda s: waits.append(s),
        )
        self.assertTrue(res1["ok"])
        self.assertFalse(res1["cached"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(waits, [2.0])

        # Segunda consulta idéntica debe salir desde SQLite http_cache sin tocar fake_get
        res2 = http_client.cached_get_json(
            source="xposedornot",
            url="https://api.xposedornot.com/v1/breach-analytics?email=a@b.com",
            query_key="a@b.com",
            ttl_seconds=3600,
            requester=fake_get,
            sleep_fn=lambda s: waits.append(s),
        )
        self.assertTrue(res2["ok"])
        self.assertTrue(res2["cached"])
        self.assertEqual(len(calls), 2)

        # breach_check.check_xposedornot_email reutiliza la misma clave en caché
        b_res = breach_check.check_xposedornot_email("a@b.com")
        self.assertEqual(len(b_res["breaches"]), 1)
        self.assertEqual(b_res["breaches"][0]["name"], "TestLeak")

    def test_frontend_has_no_external_font_cdn_dependencies(self):
        html_txt = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("fonts.googleapis.com", html_txt)
        self.assertNotIn("fonts.gstatic.com", html_txt)


if __name__ == "__main__":
    unittest.main()
