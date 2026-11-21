# -*- coding: utf-8 -*-
"""Pruebas de seguridad: anti-SSRF, protección DPAPI de clave OAuth y cabeceras CSP/anti-CSRF."""
import base64
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.responses import JSONResponse

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import web_app  # noqa: E402
from modules import platform_auth, storage, vision_analysis  # noqa: E402


def _drive_coro(coro):
    """Ejecuta corrutinas ASGI puras sin requerir sockets `socketpair` de Windows asyncio."""
    try:
        while True:
            coro.send(None)
    except StopIteration as stop:
        return stop.value


def _asgi_request(method: str, path: str, headers: dict = None, body: bytes = b"") -> tuple:
    hdr_list = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    if "host" not in {k.lower() for k in (headers or {})}:
        hdr_list.append((b"host", b"127.0.0.1:8000"))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "query_string": b"",
        "headers": hdr_list,
        "server": ("127.0.0.1", 8000),
    }
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    messages = []

    async def send(msg):
        messages.append(msg)

    async def inner_app(sc, recv, snd):
        p = sc.get("path")
        m = sc.get("method")
        if m == "GET" and p == "/":
            resp = web_app.index()
        elif m == "GET" and p == "/api/cases":
            resp = JSONResponse(web_app.api_cases_list())
        elif m == "POST" and p == "/api/cases":
            resp = JSONResponse({"ok": True}, status_code=201)
        else:
            resp = JSONResponse({"detail": "Not Found"}, status_code=404)
        await resp(sc, recv, snd)

    mw = web_app.SecurityAndCsrfMiddleware(inner_app)
    _drive_coro(mw(scope, receive, send))
    status = messages[0]["status"]
    resp_headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in messages[0]["headers"]}
    resp_body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, resp_headers, resp_body


class TestSecurityHardening(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        storage.set_db_path(Path(self._tmp.name) / "sec.db")
        storage.init_db()

    def tearDown(self):
        storage.set_db_path(None)
        self._tmp.cleanup()

    def test_anti_ssrf_blocks_private_loopback_and_malicious_redirects(self):
        blocked = [
            "http://127.0.0.1/avatar.jpg",
            "http://localhost:8000/secret.png",
            "http://169.254.169.254/latest/meta-data/",
            "http://192.168.1.10/cam.jpg",
            "http://10.0.0.5/internal.jpg",
            "http://[::1]/local.jpg",
            "file:///etc/passwd",
        ]
        for u in blocked:
            self.assertFalse(vision_analysis.is_safe_public_http_url(u), f"Debió bloquearse: {u}")
            img_b, _ = vision_analysis.download_public_image(u)
            self.assertEqual(img_b, b"")

        # Simular dominio público que redirige (302) hacia 127.0.0.1 -> debe abortar antes del salto
        fake_addrinfo = [(2, 1, 6, "", ("93.184.216.34", 443))]
        with patch("modules.vision_analysis.socket.getaddrinfo", return_value=fake_addrinfo), patch(
            "modules.vision_analysis.requests.get",
            return_value=SimpleNamespace(
                status_code=302,
                headers={"Location": "http://127.0.0.1:8000/internal.png"},
                close=lambda: None,
            ),
        ) as mock_get:
            data, _ = vision_analysis.download_public_image("https://example.com/pic.jpg")
            self.assertEqual(data, b"")
            self.assertEqual(mock_get.call_count, 1)

    def test_dpapi_and_legacy_token_key_compatibility(self):
        key_file = Path(self._tmp.name) / ".token_key"
        with patch.object(platform_auth, "KEY_FILE", key_file), patch.dict(os.environ, {"OSINT_TOKEN_SECRET": ""}, clear=False):
            enc = platform_auth.encrypt_token_payload({"access_token": "tok_dpapi_123"})
            dec = platform_auth.decrypt_token_payload(enc)
            self.assertEqual(dec["access_token"], "tok_dpapi_123")
            if os.name == "nt":
                self.assertTrue(key_file.read_bytes().startswith(b"dpapi:"))

            # Retrocompatibilidad con clave legacy en plano
            legacy_file = Path(self._tmp.name) / ".token_key_legacy"
            legacy_file.write_bytes(base64.urlsafe_b64encode(b"A" * 32))
            with patch.object(platform_auth, "KEY_FILE", legacy_file):
                enc2 = platform_auth.encrypt_token_payload({"access_token": "tok_leg"})
                self.assertEqual(platform_auth.decrypt_token_payload(enc2)["access_token"], "tok_leg")

    def test_csp_headers_and_csrf_token_middleware(self):
        st, hdrs, body = _asgi_request("GET", "/")
        self.assertEqual(st, 200)
        self.assertEqual(hdrs.get("x-content-type-options"), "nosniff")
        self.assertEqual(hdrs.get("x-frame-options"), "SAMEORIGIN")
        self.assertIn("default-src 'self'", hdrs.get("content-security-policy", ""))
        m = re.search(r'<meta name="osint-api-token" content="([^"]+)"', body.decode("utf-8"))
        self.assertIsNotNone(m)
        tok = m.group(1)

        # Petición same-origin de pestaña ya abierta en 127.0.0.1 (sin header) NO debe bloquearse
        st_same, _, _ = _asgi_request(
            "POST",
            "/api/cases",
            headers={"Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Origin": "http://127.0.0.1:8000"},
            body=b"{}",
        )
        self.assertEqual(st_same, 201)

        # Petición cross-site sin token -> 403
        st_csrf, _, _ = _asgi_request(
            "POST", "/api/cases", headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.test"}, body=b"{}"
        )
        self.assertEqual(st_csrf, 403)

        # Token inválido -> 401
        st_bad, _, _ = _asgi_request("GET", "/api/cases", headers={"X-OSINT-Token": "wrong-token"})
        self.assertEqual(st_bad, 401)

        # Modo estricto: requiere token incluso en GET
        with patch.dict(os.environ, {"OSINT_REQUIRE_API_TOKEN": "1"}, clear=False):
            st_no, _, _ = _asgi_request("GET", "/api/cases")
            self.assertEqual(st_no, 401)
            st_ok, _, _ = _asgi_request("GET", "/api/cases", headers={"X-OSINT-Token": tok})
            self.assertEqual(st_ok, 200)


if __name__ == "__main__":
    unittest.main()
