#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OSINT-Auto :: Servidor Web (FastAPI) y API REST
Ensambla la aplicación FastAPI, los archivos estáticos (`web/`), SQLite y los routers
modularizados por dominio (`routers/`), manteniendo re-exportaciones compatibles.
"""
import hmac
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

# Permite ejecutar el servidor desde cualquier directorio
ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

# Carga opcional de `.env` (HIBP_API_KEY, BLACKBIRD_PATH)
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT_DIR / ".env")
except ImportError:
    pass

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from modules import storage  # noqa: E402
from modules.logger import get_logger  # noqa: E402
from routers.runtime import (  # noqa: E402
    WEB_DIR,
    _JOBS,
    _JOBS_LOCK,
    _create_job,
    _list_folder_files,
    _now_iso,
    _require_legitimate,
    _run_in_background,
    _update_job,
    _validate_case_id,
    get_active_api_token,
)
from routers.schemas import *  # noqa: E402, F403
from routers import agent_and_friends, analysis, cases, search_and_breach  # noqa: E402

# Re-exportaciones directas de handlers para pruebas e invocación directa (`web_app.api_*`)
from routers.search_and_breach import (  # noqa: E402
    _CIUDADES_CATALOGO,
    _LOC_CACHE,
    _MAX_MEDIA_BYTES,
    _PAISES_CATALOGO,
    _norm_txt,
    api_breach_email,
    api_breach_password,
    api_job_detail,
    api_jobs,
    api_locations_suggest,
    api_media_inspect,
    api_report_file,
    api_reports,
    api_search_email,
    api_search_full,
    api_search_name,
    api_search_phone,
    api_search_social,
    api_search_username,
    api_status,
    index,
)
from routers.cases import (  # noqa: E402
    _MAX_IMPORT_BYTES,
    api_case_add_identifier,
    api_case_add_profile,
    api_case_content,
    api_case_delete,
    api_case_delete_identifier,
    api_case_detail,
    api_case_import_job,
    api_case_ingest_connector,
    api_case_ingest_import,
    api_case_patch_profile,
    api_case_resolve,
    api_case_update,
    api_cases_create,
    api_cases_list,
    api_connectors_list,
    api_ingest_template,
)
from routers.analysis import (  # noqa: E402
    _GALLERY_EXTS,
    _MAX_GALLERY_IMAGE_BYTES,
    api_case_exposure,
    api_case_generate_report,
    api_case_geo_hints,
    api_case_geo_hints_recompute,
    api_case_location_history,
    api_case_media_geo_investigate,
    api_case_media_upload,
    api_case_nlp_analyze,
    api_case_nlp_get,
    api_case_sna,
    api_case_sna_add_interaction,
    api_case_sna_recompute,
    api_case_subject_profile,
    api_case_subject_profile_recompute,
    api_case_subject_profile_review,
    api_case_vision_analyze,
    api_case_vision_summary,
    api_maintenance_cleanup,
    api_maintenance_storage,
    api_nlp_status,
    api_vision_status,
)
from routers.agent_and_friends import (  # noqa: E402
    api_agent_case_runs,
    api_agent_clean_cache,
    api_agent_clear_session,
    api_agent_commit_run,
    api_agent_delete_item,
    api_agent_open_session,
    api_agent_patch_item,
    api_agent_run_cancel,
    api_agent_run_detail,
    api_agent_run_pause,
    api_agent_run_resume,
    api_agent_sessions,
    api_agent_start,
    api_auth_connections,
    api_auth_delete_connection,
    api_auth_oauth_callback,
    api_auth_oauth_start,
    api_case_add_friend,
    api_case_delete_friend,
    api_case_evidences,
    api_case_friend_crawl,
    api_case_friend_detail,
    api_case_friend_evidence,
    api_case_friend_investigate,
    api_case_friend_link_sna,
    api_case_friend_scan_start,
    api_case_friend_scan_status,
    api_case_interactants,
    api_case_investigate_account,
    api_case_list_friends,
    api_case_relational_analyze,
    api_case_relational_list,
    api_case_relational_review,
    api_platforms_capabilities,
)

app = FastAPI(
    title="OSINT-Auto Web API",
    description="Panel web y endpoints REST para OSINT-Auto",
    version="2.0.0",
)

_SPA_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob: https:; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; "
    "object-src 'none'; "
    "base-uri 'self'"
)


class SecurityAndCsrfMiddleware:
    """Middleware ASGI puro para cabeceras CSP/seguridad, anti-CSRF y token de sesión local."""

    # Para qué sirve: Inicializa el middleware envolviendo la aplicación ASGI subyacente.
    # Cómo funciona: Almacena la referencia `asgi_app` en `self.app`.
    def __init__(self, asgi_app):
        self.app = asgi_app

    # Para qué sirve: Intercepta peticiones HTTP entrantes para validar token API, protección CSRF e inyectar cabeceras seguras.
    # Cómo funciona: Verifica `X-OSINT-Token`/Bearer y origen cross-site en `/api/` y envuelve `send` con `send_with_security_headers`.
    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        method = (scope.get("method") or "GET").upper()
        raw_hdrs = {
            k.decode("latin-1").lower(): v.decode("latin-1")
            for k, v in (scope.get("headers") or [])
        }
        is_api = path.startswith("/api/")
        is_oauth_cb = path.startswith("/api/auth/") and path.endswith("/callback")
        is_report_get = method == "GET" and path.startswith("/api/reports/")

        if is_api and not is_oauth_cb and not is_report_get:
            expected = get_active_api_token()
            provided = (raw_hdrs.get("x-osint-token") or "").strip()
            if not provided:
                auth = (raw_hdrs.get("authorization") or "").strip()
                if auth.lower().startswith("bearer "):
                    provided = auth[7:].strip()
            token_ok = bool(provided) and hmac.compare_digest(provided, expected)

            if provided and not token_ok:
                resp = JSONResponse({"detail": "Token de sesión inválido."}, status_code=401)
                await resp(scope, receive, send)
                return

            strict_env = bool(os.environ.get("OSINT_API_TOKEN", "").strip()) or (
                os.environ.get("OSINT_REQUIRE_API_TOKEN", "").strip().lower() in ("1", "true", "yes")
            )
            bind_host = os.environ.get("OSINT_WEB_HOST", "127.0.0.1").strip().lower()
            non_loopback = bind_host not in ("127.0.0.1", "localhost", "::1", "")

            if (strict_env or non_loopback) and path != "/api/status" and not token_ok:
                resp = JSONResponse({"detail": "Autenticación API requerida (X-OSINT-Token)."}, status_code=401)
                await resp(scope, receive, send)
                return

            if method in ("POST", "PUT", "PATCH", "DELETE"):
                sec_site = (raw_hdrs.get("sec-fetch-site") or "").strip().lower()
                origin = (raw_hdrs.get("origin") or "").strip()
                host_hdr = (raw_hdrs.get("host") or "").strip().lower()
                cross_origin = sec_site == "cross-site"
                if not cross_origin and origin:
                    orig_netloc = urlparse(origin).netloc.lower()
                    if host_hdr and orig_netloc and orig_netloc != host_hdr:
                        cross_origin = True
                if cross_origin and not token_ok:
                    resp = JSONResponse({"detail": "Petición cross-origin bloqueada (CSRF)."}, status_code=403)
                    await resp(scope, receive, send)
                    return

        # Para qué sirve: Añade cabeceras de seguridad HTTP y política CSP a la respuesta saliente.
        # Cómo funciona: Al recibir `http.response.start`, agrega `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` y `CSP` si no existen.
        async def send_with_security_headers(message):
            if message.get("type") == "http.response.start":
                hdrs = list(message.get("headers") or [])
                existing = {k.decode("latin-1").lower() for k, _ in hdrs}
                extra = [
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"SAMEORIGIN"),
                    (b"referrer-policy", b"strict-origin-when-cross-origin"),
                ]
                if not path.startswith("/api/reports/"):
                    extra.append((b"content-security-policy", _SPA_CSP.encode("latin-1")))
                for hk, hv in extra:
                    if hk.decode("latin-1") not in existing:
                        hdrs.append((hk, hv))
                message = {**message, "headers": hdrs}
            await send(message)

        await self.app(scope, receive, send_with_security_headers)


app.add_middleware(SecurityAndCsrfMiddleware)

# Archivos estáticos (CSS y JS de la interfaz)
if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

# Inicializar base de datos local SQLite y recuperar jobs huérfanos de reinicios previos
storage.init_db()
_recovered_jobs = storage.recover_orphaned_jobs()
if _recovered_jobs:
    get_logger("web_app").warning("Recuperados %s job(s) huérfanos en estado running tras reinicio.", _recovered_jobs)

# Registrar routers modularizados
app.include_router(search_and_breach.router)
app.include_router(cases.router)
app.include_router(analysis.router)
app.include_router(agent_and_friends.router)


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("OSINT_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("OSINT_WEB_PORT", "8000"))
    print(f"\n  OSINT-Auto Web UI disponible en -> http://{host}:{port}\n")
    uvicorn.run(app, host=host, port=port)
