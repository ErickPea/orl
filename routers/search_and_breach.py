# -*- coding: utf-8 -*-
"""Router de búsquedas OSINT rápidas, brechas, media/EXIF, ubicaciones, jobs y reportes."""
import html
import json
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse

from modules import reports, storage, utils
from modules.logger import get_logger
from modules.blackbird import run_blackbird
from modules.breach_check import (
    breaches_csv_rows,
    check_email_breaches,
    check_password_k_anonymity,
    format_breach_report,
)
from modules.connectors import connectors_catalog
from modules.email_search import DNS_OK, run_email_lookup, run_email_osint, run_email_search
from modules.media_meta import format_media_report, inspect_media, media_capabilities
from modules.name_search import (
    format_name_report,
    name_csv_rows,
    name_search_capabilities,
    run_name_location_search,
)
from modules.phone_search import PHONENUMBERS_OK, run_phone_search
from modules.social_enrich import (
    build_footprint_graph,
    build_privacy_assessment,
    enrich_social_profiles,
)
from modules.username_search import run_username_search
from routers.runtime import (
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
from routers.schemas import (
    BreachEmailRequest,
    BreachPasswordRequest,
    EmailRequest,
    FullRequest,
    NameLocationRequest,
    PhoneRequest,
    SocialRequest,
    UsernameRequest,
)

router = APIRouter()

# Límite razonable para subida local de fotos/videos (150 MB)
_MAX_MEDIA_BYTES = 150 * 1024 * 1024

_LOC_CACHE: dict = {}

_PAISES_CATALOGO = [
    {"name": "Colombia", "code": "CO"},
    {"name": "México", "code": "MX"},
    {"name": "España", "code": "ES"},
    {"name": "Argentina", "code": "AR"},
    {"name": "Chile", "code": "CL"},
    {"name": "Perú", "code": "PE"},
    {"name": "Ecuador", "code": "EC"},
    {"name": "Venezuela", "code": "VE"},
    {"name": "Uruguay", "code": "UY"},
    {"name": "Estados Unidos", "code": "US"},
    {"name": "Panamá", "code": "PA"},
    {"name": "Costa Rica", "code": "CR"},
    {"name": "República Dominicana", "code": "DO"},
    {"name": "Guatemala", "code": "GT"},
    {"name": "Bolivia", "code": "BO"},
    {"name": "Paraguay", "code": "PY"},
    {"name": "Honduras", "code": "HN"},
    {"name": "El Salvador", "code": "SV"},
    {"name": "Nicaragua", "code": "NI"},
    {"name": "Puerto Rico", "code": "PR"},
    {"name": "Brasil", "code": "BR"},
    {"name": "Canadá", "code": "CA"},
    {"name": "Reino Unido", "code": "GB"},
    {"name": "Francia", "code": "FR"},
    {"name": "Alemania", "code": "DE"},
    {"name": "Italia", "code": "IT"},
]

_CIUDADES_CATALOGO = [
    # Colombia
    {"city": "Bogotá", "region": "Cundinamarca", "country": "Colombia"},
    {"city": "Medellín", "region": "Antioquia", "country": "Colombia"},
    {"city": "Cali", "region": "Valle del Cauca", "country": "Colombia"},
    {"city": "Barranquilla", "region": "Atlántico", "country": "Colombia"},
    {"city": "Cartagena", "region": "Bolívar", "country": "Colombia"},
    {"city": "Bucaramanga", "region": "Santander", "country": "Colombia"},
    {"city": "Pereira", "region": "Risaralda", "country": "Colombia"},
    {"city": "Santa Marta", "region": "Magdalena", "country": "Colombia"},
    {"city": "Manizales", "region": "Caldas", "country": "Colombia"},
    {"city": "Cúcuta", "region": "Norte de Santander", "country": "Colombia"},
    {"city": "Ibagué", "region": "Tolima", "country": "Colombia"},
    {"city": "Villavicencio", "region": "Meta", "country": "Colombia"},
    {"city": "Pasto", "region": "Nariño", "country": "Colombia"},
    {"city": "Montería", "region": "Córdoba", "country": "Colombia"},
    {"city": "Neiva", "region": "Huila", "country": "Colombia"},
    {"city": "Armenia", "region": "Quindío", "country": "Colombia"},
    {"city": "Popayán", "region": "Cauca", "country": "Colombia"},
    {"city": "Valledupar", "region": "Cesar", "country": "Colombia"},
    {"city": "Sincelejo", "region": "Sucre", "country": "Colombia"},
    {"city": "Tunja", "region": "Boyacá", "country": "Colombia"},
    {"city": "Riohacha", "region": "La Guajira", "country": "Colombia"},
    {"city": "Envigado", "region": "Antioquia", "country": "Colombia"},
    {"city": "Bello", "region": "Antioquia", "country": "Colombia"},
    {"city": "Itagüí", "region": "Antioquia", "country": "Colombia"},
    {"city": "Soacha", "region": "Cundinamarca", "country": "Colombia"},
    {"city": "Chía", "region": "Cundinamarca", "country": "Colombia"},
    {"city": "Palmira", "region": "Valle del Cauca", "country": "Colombia"},
    {"city": "Floridablanca", "region": "Santander", "country": "Colombia"},
    # México
    {"city": "Ciudad de México", "region": "CDMX", "country": "México"},
    {"city": "Guadalajara", "region": "Jalisco", "country": "México"},
    {"city": "Monterrey", "region": "Nuevo León", "country": "México"},
    {"city": "Puebla", "region": "Puebla", "country": "México"},
    {"city": "Tijuana", "region": "Baja California", "country": "México"},
    {"city": "Querétaro", "region": "Querétaro", "country": "México"},
    {"city": "Mérida", "region": "Yucatán", "country": "México"},
    {"city": "Cancún", "region": "Quintana Roo", "country": "México"},
    # España
    {"city": "Madrid", "region": "Comunidad de Madrid", "country": "España"},
    {"city": "Barcelona", "region": "Cataluña", "country": "España"},
    {"city": "Valencia", "region": "Comunidad Valenciana", "country": "España"},
    {"city": "Sevilla", "region": "Andalucía", "country": "España"},
    {"city": "Málaga", "region": "Andalucía", "country": "España"},
    {"city": "Bilbao", "region": "País Vasco", "country": "España"},
    # Latam & US
    {"city": "Buenos Aires", "region": "CABA", "country": "Argentina"},
    {"city": "Córdoba", "region": "Córdoba", "country": "Argentina"},
    {"city": "Rosario", "region": "Santa Fe", "country": "Argentina"},
    {"city": "Santiago", "region": "Región Metropolitana", "country": "Chile"},
    {"city": "Valparaíso", "region": "Valparaíso", "country": "Chile"},
    {"city": "Lima", "region": "Lima", "country": "Perú"},
    {"city": "Arequipa", "region": "Arequipa", "country": "Perú"},
    {"city": "Cusco", "region": "Cusco", "country": "Perú"},
    {"city": "Quito", "region": "Pichincha", "country": "Ecuador"},
    {"city": "Guayaquil", "region": "Guayas", "country": "Ecuador"},
    {"city": "Caracas", "region": "Distrito Capital", "country": "Venezuela"},
    {"city": "Maracaibo", "region": "Zulia", "country": "Venezuela"},
    {"city": "Montevideo", "region": "Montevideo", "country": "Uruguay"},
    {"city": "Ciudad de Panamá", "region": "Panamá", "country": "Panamá"},
    {"city": "San José", "region": "San José", "country": "Costa Rica"},
    {"city": "Miami", "region": "Florida", "country": "Estados Unidos"},
    {"city": "Nueva York", "region": "New York", "country": "Estados Unidos"},
    {"city": "Los Ángeles", "region": "California", "country": "Estados Unidos"},
    {"city": "Houston", "region": "Texas", "country": "Estados Unidos"},
]


def _norm_txt(text: str) -> str:
    """Normaliza a minúsculas sin tildes para búsquedas de autocompletado tolerantes."""
    from modules.name_search import strip_accents

    return strip_accents(text or "").lower().strip()


@router.get("/", include_in_schema=False)
def index():
    """Sirve la interfaz web principal inyectando el token de sesión anti-CSRF en `<head>`."""
    index_file = WEB_DIR / "index.html"
    if not index_file.is_file():
        raise HTTPException(status_code=404, detail="No se encontró web/index.html")
    raw_html = index_file.read_text(encoding="utf-8")
    tok = html.escape(get_active_api_token(), quote=True)
    meta_tag = f'  <meta name="osint-api-token" content="{tok}" />\n</head>'
    if "</head>" in raw_html:
        raw_html = raw_html.replace("</head>", meta_tag, 1)
    return HTMLResponse(
        content=raw_html,
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@router.get("/api/status")
def api_status():
    """Devuelve disponibilidad de herramientas OSINT, librerías, HIBP y contadores."""
    tools = {name: utils.tool_available(name) for name in utils.TOOLS}
    reportes = utils.list_reports()
    with _JOBS_LOCK:
        active_jobs = sum(1 for j in _JOBS.values() if j["status"] == "running")
    return {
        "tools": tools,
        "capabilities": {
            "phonenumbers": PHONENUMBERS_OK,
            "dnspython": DNS_OK,
            "hibp_api_key": bool(os.environ.get("HIBP_API_KEY", "").strip()),
            "xposedornot": True,
            "k_anonymity": True,
            **media_capabilities(),
            **name_search_capabilities(),
            "sqlite_cases": True,
            "connectors_count": len(connectors_catalog()),
        },
        "reports_count": len(reportes),
        "cases_count": storage.count_cases(),
        "active_jobs": active_jobs,
    }


@router.post("/api/search/username")
def api_search_username(req: UsernameRequest):
    _require_legitimate(req.confirm_legitimate)
    case_id = _validate_case_id(req.case_id)
    username = req.username.strip()
    country = (req.country or "").strip().lower() or None
    job = _create_job("username", f"{username} ({country})" if country else username, case_id=case_id)

    def worker(job_id: str):
        prefix = f"geo_{country}_{username}" if country else f"user_{username}"
        out_dir = utils.make_output_dir(prefix)
        _update_job(job_id, step="Ejecutando Maigret y Sherlock...", progress=20,
                    log=f"Carpeta creada: {out_dir.name}", folder=out_dir.name)

        run_username_search(username, out_dir, country=country, top=req.top)

        _update_job(job_id, step="Consolidando perfiles y calculando confianza...", progress=80,
                    log="Cruzando hallazgos y generando reportes HTML/PDF/CSV...")
        profiles = reports.consolidate_profiles(out_dir)
        insights = reports.build_insights(profiles)
        signals = reports.extract_identity_signals(profiles)
        etiqueta = f"{username} ({country})" if country else f"username:{username}"
        if profiles:
            reports.write_csv(out_dir, reports.rows_for_csv(profiles))
        reports.write_html(out_dir, etiqueta, insights)
        reports.write_pdf(out_dir, etiqueta, insights)

        summary = {
            "total": len(profiles),
            "alta": sum(1 for p in profiles if p["confianza"] == "ALTA"),
            "media": sum(1 for p in profiles if p["confianza"] == "MEDIA"),
            "baja": sum(1 for p in profiles if p["confianza"] == "BAJA"),
        }
        privacy = build_privacy_assessment(profiles=profiles, signals=signals)
        graph = build_footprint_graph(username, profiles=profiles, signals=signals)
        _update_job(
            job_id,
            status="done",
            step="Completado",
            progress=100,
            finished_at=_now_iso(),
            files=_list_folder_files(out_dir),
            log=f"Finalizado: {summary['total']} perfiles únicos ({summary['alta']} ALTA).",
            result={
                "kind": "username",
                "username": username,
                "country": country,
                "summary": summary,
                "profiles": profiles,
                "signals": signals,
                "privacy": privacy,
                "graph": graph,
            },
        )

    _run_in_background(job["id"], worker)
    return {"job_id": job["id"], "status": "running"}


@router.post("/api/search/social")
def api_search_social(req: SocialRequest):
    """
    Modo enfocado en Redes Sociales:
      1. Ejecuta Maigret con tag `social` (+ país opcional) y Sherlock.
      2. Consolida perfiles y filtra plataformas sociales.
      3. Enriquece únicamente metadatos públicos abiertos (GitHub/Reddit API pública y Open Graph)
         marcando explícitamente como `login_wall` las redes que exigen iniciar sesión.
      4. Genera grafo de huella digital, evaluación de privacidad y reportes HTML/PDF/CSV.
    """
    _require_legitimate(req.confirm_legitimate)
    case_id = _validate_case_id(req.case_id)
    username = req.username.strip()
    country = (req.country or "").strip().lower() or None
    job = _create_job("social", f"{username} ({country})" if country else username, case_id=case_id)

    def worker(job_id: str):
        out_dir = utils.make_output_dir(f"social_{username}")
        _update_job(job_id, step="Paso 1/3 :: Rastreo enfocado en redes sociales...", progress=20,
                    log=f"Ejecutando Maigret (--tags social) y Sherlock en {out_dir.name}", folder=out_dir.name)
        run_username_search(username, out_dir, country=country, top=300, social_only=True)

        _update_job(job_id, step="Paso 2/3 :: Consolidando y leyendo metadatos públicos abiertos...", progress=65,
                    log="Filtrando redes sociales e inspeccionando metadatos públicos sin login...")
        all_profiles = reports.consolidate_profiles(out_dir)
        social_profiles = enrich_social_profiles(
            all_profiles, max_checks=req.max_enrich, fetch_online=req.fetch_public_meta
        )
        signals = reports.extract_identity_signals(all_profiles)
        privacy = build_privacy_assessment(profiles=all_profiles, signals=signals)
        graph = build_footprint_graph(username, profiles=social_profiles or all_profiles, signals=signals)

        _update_job(job_id, step="Paso 3/3 :: Generando reportes HTML, PDF y CSV...", progress=90,
                    log="Guardando resultados de redes sociales y privacidad...")
        insights = reports.build_insights(social_profiles or all_profiles)
        extra = {
            "Evaluación de exposición y privacidad": [
                f"Nivel de exposición: {privacy['level']} ({privacy['score']}/100)",
                *privacy["findings"],
            ],
            "Acciones recomendadas de privacidad": privacy["actions"],
        }
        if social_profiles:
            reports.write_csv(out_dir, reports.rows_for_csv(social_profiles), filename="redes_sociales.csv")
        if all_profiles:
            reports.write_csv(out_dir, reports.rows_for_csv(all_profiles))
        reports.write_html(out_dir, f"social:{username}", insights, extra)
        reports.write_pdf(out_dir, f"social:{username}", {**insights, **extra})

        summary = {
            "total": len(social_profiles),
            "alta": sum(1 for p in social_profiles if p["confianza"] == "ALTA"),
            "media": sum(1 for p in social_profiles if p["confianza"] == "MEDIA"),
            "baja": sum(1 for p in social_profiles if p["confianza"] == "BAJA"),
            "public_ok": sum(1 for p in social_profiles if (p.get("public_meta") or {}).get("status") == "public_ok"),
            "login_wall": sum(1 for p in social_profiles if (p.get("public_meta") or {}).get("status") == "login_wall"),
        }
        _update_job(
            job_id,
            status="done",
            step="Completado",
            progress=100,
            finished_at=_now_iso(),
            files=_list_folder_files(out_dir),
            log=f"Finalizado: {summary['total']} redes sociales ({summary['public_ok']} con metadatos públicos, {summary['login_wall']} con muro de login).",
            result={
                "kind": "social",
                "username": username,
                "country": country,
                "summary": summary,
                "social_profiles": social_profiles,
                "signals": signals,
                "privacy": privacy,
                "graph": graph,
            },
        )

    _run_in_background(job["id"], worker)
    return {"job_id": job["id"], "status": "running"}


@router.post("/api/search/name")
def api_search_name(req: NameLocationRequest):
    """
    Búsqueda legal por Nombre y Ubicación/Zona:
      - Consulta APIs públicas abiertas (GitHub, ORCID, OpenAlex, Wikipedia) y,
        si existen claves en `.env`, Google Custom Search y/o Brave Search.
      - Genera matriz de consultas por red social, sugerencias de usernames para pivotar
        y reportes HTML/PDF/CSV.
    """
    _require_legitimate(req.confirm_legitimate)
    case_id = _validate_case_id(req.case_id)
    full_name = req.full_name.strip()
    city = (req.city or "").strip()
    country = (req.country or "").strip()
    keyword = (req.keyword or "").strip()
    etiqueta = f"{full_name} ({city or country or 'Global'})"
    job = _create_job("name", etiqueta, case_id=case_id)

    def worker(job_id: str):
        out_dir = utils.make_output_dir(f"name_{full_name}")
        _update_job(job_id, step="Consultando APIs públicas y generando matriz por red social...", progress=30,
                    log=f"Buscando '{full_name}' en zona '{city or country or 'Global'}'...", folder=out_dir.name)
        res = run_name_location_search(
            full_name=full_name,
            city=city,
            country=country,
            keyword=keyword,
            selected_ids=req.platforms,
        )

        _update_job(job_id, step="Generando reportes HTML, PDF y CSV...", progress=85,
                    log=f"Encontrados {res['summary']['total']} candidatos automáticos + {res['summary']['dorks_count']} consultas por red.")
        secciones = format_name_report(res)
        reports.write_html(out_dir, f"name:{etiqueta}", secciones)
        reports.write_pdf(out_dir, f"name:{etiqueta}", secciones)
        if res.get("candidates"):
            reports.write_csv(out_dir, name_csv_rows(res), filename="busqueda_nombre.csv")
        (out_dir / "busqueda_nombre.json").write_text(
            json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        graph_profiles = [
            {
                "sitio": f"{c['network']} · {c['person_name'][:22]}",
                "url": c.get("url"),
                "confianza": c.get("match", "MEDIA"),
                "fuentes": c.get("source", "API"),
                "metadata_resumen": c.get("location") or "",
            }
            for c in res.get("candidates") or []
        ]
        graph = build_footprint_graph(full_name, profiles=graph_profiles)

        _update_job(
            job_id,
            status="done",
            step="Completado",
            progress=100,
            finished_at=_now_iso(),
            files=_list_folder_files(out_dir),
            log=f"Finalizado: {res['summary']['total']} candidatos ({res['summary']['alta']} coincidencia ALTA).",
            result={"kind": "name", **res, "graph": graph},
        )

    _run_in_background(job["id"], worker)
    return {"job_id": job["id"], "status": "running"}


@router.post("/api/search/email")
def api_search_email(req: EmailRequest):
    _require_legitimate(req.confirm_legitimate)
    case_id = _validate_case_id(req.case_id)
    target = req.target.strip()
    if req.mode in ("lookup", "osint") and "@" not in target:
        raise HTTPException(status_code=400, detail="Ingresa un correo electrónico válido (con '@').")

    job = _create_job(f"email:{req.mode}", target, case_id=case_id)

    def worker(job_id: str):
        if req.mode == "generate":
            out_dir = utils.make_output_dir(f"email_{target}")
            _update_job(job_id, step="Generando y verificando correos con Holehe...", progress=25,
                        log=f"Probando hasta {req.limit} correos derivados de '{target}'...",
                        folder=out_dir.name)
            found = run_email_search(target, out_dir, limit=req.limit)
            if found:
                reports.write_csv(out_dir, [{"email": e, "servicios": ";".join(s)} for e, s in found.items()])
            ins = {"Correos registrados": [f"{e}: {', '.join(s)}" for e, s in found.items()] or ["(ninguno)"]}
            reports.write_html(out_dir, f"email:{target}", ins)
            reports.write_pdf(out_dir, f"email:{target}", ins)
            _update_job(
                job_id,
                status="done",
                step="Completado",
                progress=100,
                finished_at=_now_iso(),
                files=_list_folder_files(out_dir),
                log=f"Finalizado: {len(found)} correos con registros confirmados.",
                result={"kind": "email_generate", "username": target, "found": found},
            )
            return

        out_dir = utils.make_output_dir(f"mail_{target.replace('@', '_at_')}")
        _update_job(job_id, step="Validando DNS/MX, Gravatar y Holehe...", progress=20,
                    log=f"Carpeta creada: {out_dir.name}", folder=out_dir.name)

        if req.mode == "osint":
            res = run_email_osint(target, out_dir)
            services = res.get("services", [])
            derived = res.get("profiles_found", {})
            usernames_tested = res.get("usernames_tested", [])
        else:
            res = run_email_lookup(target, out_dir)
            services = res.get("registered_details") or res.get("registered", [])
            derived = {}
            usernames_tested = []

        _update_job(job_id, step="Consultando brechas de datos...", progress=70,
                    log="Consultando XposedOrNot / HIBP...")
        breach_res = check_email_breaches(target, out_dir=out_dir)

        _update_job(job_id, step="Generando reportes HTML, PDF y CSV...", progress=90,
                    log="Escribiendo reportes finales...")
        mx = res.get("mx") or {}
        grav = res.get("gravatar") or {}
        rate_limited = res.get("rate_limited", [])
        grav_info = (
            [
                f"Perfil: {grav.get('profile_url') or 'sí'}",
                f"Nombre visible: {grav.get('display_name') or '—'}",
                f"Username: {grav.get('preferred_username') or '—'}",
                f"Ubicación: {grav.get('location') or '—'}",
                *(grav.get("accounts") or []),
            ]
            if grav.get("found")
            else ["Sin perfil público en Gravatar"]
        )
        extra = {
            "Correo": target,
            "Validación DNS / MX": f"{mx.get('note', '—')} | MX: {', '.join(mx.get('mx_records') or []) or '—'}",
            "Gravatar público": grav_info,
            "Servicios registrados (Holehe)": services or ["(ninguno confirmado)"],
            "Servicios con rate-limit (no concluyentes)": (
                [f"{len(rate_limited)} servicios bloqueados: {', '.join(rate_limited[:20])}"]
                if rate_limited
                else ["Ninguno"]
            ),
            **format_breach_report(breach_res),
            "Usernames derivados probados": usernames_tested or ["—"],
            "Perfiles asociados a usernames derivados": derived or {"—": "sin hits"},
        }
        reports.write_html(out_dir, f"mail:{target}", extra)
        reports.write_pdf(out_dir, f"mail:{target}", extra)
        if services:
            reports.write_csv(out_dir, [{"email": target, "servicio": s, "estado": "registrado"} for s in services])
        if breach_res.get("breaches"):
            reports.write_csv(out_dir, breaches_csv_rows(breach_res), filename="brechas_detectadas.csv")

        _update_job(
            job_id,
            status="done",
            step="Completado",
            progress=100,
            finished_at=_now_iso(),
            files=_list_folder_files(out_dir),
            log=f"Finalizado: {len(services)} servicios registrados, {breach_res['breach_count']} brechas.",
            result={
                "kind": "email",
                "email": target,
                "mode": req.mode,
                "mx": mx,
                "gravatar": grav,
                "services": services,
                "rate_limited": rate_limited,
                "breaches": breach_res,
                "usernames_tested": usernames_tested,
                "profiles_found": derived,
            },
        )

    _run_in_background(job["id"], worker)
    return {"job_id": job["id"], "status": "running"}


@router.post("/api/search/phone")
def api_search_phone(req: PhoneRequest):
    _require_legitimate(req.confirm_legitimate)
    case_id = _validate_case_id(req.case_id)
    phone = req.phone.strip()
    job = _create_job("phone", phone, case_id=case_id)

    def worker(job_id: str):
        out_dir = utils.make_output_dir(f"phone_{phone.replace('+', '')}")
        _update_job(job_id, step="Analizando número y consultando Ignorant...", progress=30,
                    log=f"Carpeta creada: {out_dir.name}", folder=out_dir.name)

        res = run_phone_search(phone, out_dir)
        an = res.get("analysis") or {}
        voip = res.get("voip_assessment") or {}
        secciones = {
            "Análisis técnico del número": [
                f"Formato E.164: {an.get('e164') or phone}",
                f"Válido: {'Sí' if an.get('valid') else 'No / Revisar'}",
                f"País / Región: {an.get('location') or an.get('region_code') or 'desconocido'}",
                f"Operador (carrier): {an.get('carrier') or 'no determinado'}",
                f"Tipo de línea: {an.get('line_type') or 'desconocido'}",
                f"Evaluación VoIP/virtual: {voip.get('note') or '—'}",
                f"Zonas horarias: {', '.join(an.get('timezones') or []) or '—'}",
            ],
            "Variantes de formato": res.get("variants") or [phone],
            "Consultas OSINT sugeridas": [f"{d['label']}: {d['query']}" for d in (res.get("dorks") or [])] or ["—"],
            "Servicios registrados (Ignorant)": res.get("registered") or ["(ninguno confirmado)"],
            "Servicios no registrados": res.get("not_used") or ["—"],
            "Servicios con rate-limit (no concluyentes)": res.get("rate_limited") or ["Ninguno"],
        }
        reports.write_html(out_dir, f"phone:{res.get('phone', phone)}", secciones)
        reports.write_pdf(out_dir, f"phone:{res.get('phone', phone)}", secciones)
        filas_csv = [
            {"telefono": res.get("phone", phone), "servicio": s, "estado": "registrado"}
            for s in res.get("registered", [])
        ] + [
            {"telefono": res.get("phone", phone), "servicio": s, "estado": "rate_limit"}
            for s in res.get("rate_limited", [])
        ]
        if filas_csv:
            reports.write_csv(out_dir, filas_csv, filename="telefono_servicios.csv")

        _update_job(
            job_id,
            status="done",
            step="Completado",
            progress=100,
            finished_at=_now_iso(),
            files=_list_folder_files(out_dir),
            log=f"Finalizado: {len(res.get('registered', []))} servicios confirmados.",
            result={
                "kind": "phone",
                "phone": res.get("phone", phone),
                "analysis": an,
                "registered": res.get("registered", []),
                "not_used": res.get("not_used", []),
                "rate_limited": res.get("rate_limited", []),
                "warning": res.get("warning"),
                "error": res.get("error"),
            },
        )

    _run_in_background(job["id"], worker)
    return {"job_id": job["id"], "status": "running"}


@router.post("/api/search/full")
def api_search_full(req: FullRequest):
    _require_legitimate(req.confirm_legitimate)
    case_id = _validate_case_id(req.case_id)
    username = req.username.strip()
    country = (req.country or "").strip().lower() or None
    known_email = (req.known_email or "").strip() or None
    phone = (req.phone or "").strip() or None
    job = _create_job("full", username, case_id=case_id)

    def worker(job_id: str):
        out_dir = utils.make_output_dir(f"full_{username}")
        _update_job(job_id, step="Paso 1/5 :: Username (Maigret + Sherlock)", progress=12,
                    log=f"Iniciando pipeline completo en {out_dir.name}", folder=out_dir.name)
        run_username_search(username, out_dir, country=country, top=300)

        _update_job(job_id, step="Paso 2/5 :: Correos (Holehe + Gravatar/MX)", progress=35,
                    log="Verificando correos derivados y correo conocido...")
        correos = run_email_search(username, out_dir, limit=8)
        if known_email and "@" in known_email:
            known_res = run_email_osint(known_email, out_dir)
            if known_res.get("services"):
                correos[known_email] = known_res["services"]

        _update_job(job_id, step="Paso 3/5 :: Blackbird", progress=58,
                    log="Ejecutando Blackbird para ampliar cobertura...")
        correo_ref = known_email if (known_email and "@" in known_email) else next(iter(correos), None)
        run_blackbird(username, out_dir, email=correo_ref)

        phone_res = None
        if phone:
            _update_job(job_id, step="Paso 4/5 :: Teléfono (phonenumbers + Ignorant)", progress=72,
                        log=f"Analizando teléfono {phone}...")
            phone_res = run_phone_search(phone, out_dir)
        else:
            _update_job(job_id, step="Paso 4/5 :: Teléfono (omitido)", progress=72,
                        log="Teléfono no proporcionado; paso omitido.")

        breach_res = None
        if correo_ref:
            _update_job(job_id, step="Paso 5/5 :: Brechas de datos y consolidación", progress=86,
                        log=f"Consultando brechas para {correo_ref}...")
            breach_res = check_email_breaches(correo_ref, out_dir=out_dir)
        else:
            _update_job(job_id, step="Paso 5/5 :: Consolidación y reportes", progress=86,
                        log="Sin correo confirmado para brechas; consolidando perfiles...")

        profiles = reports.consolidate_profiles(out_dir)
        insights = reports.build_insights(profiles)
        signals = reports.extract_identity_signals(profiles)

        extra = {
            "Correos registrados": [f"{e}: {', '.join(s)}" for e, s in correos.items()] or ["(ninguno)"],
        }
        if breach_res:
            extra.update(format_breach_report(breach_res))
        if phone_res:
            an = phone_res.get("analysis") or {}
            extra["Teléfono"] = [
                f"Número: {phone_res.get('phone', phone)}",
                f"País/Operador/Tipo: {an.get('location') or '?'} / {an.get('carrier') or '?'} / {an.get('line_type') or '?'}",
                f"Ignorant registrados: {', '.join(phone_res.get('registered') or []) or 'ninguno'}",
                f"Ignorant rate-limit: {', '.join(phone_res.get('rate_limited') or []) or 'ninguno'}",
            ]
        else:
            extra["Teléfono"] = "no proporcionado"

        if profiles:
            reports.write_csv(out_dir, reports.rows_for_csv(profiles))
        reports.write_html(out_dir, f"FULL:{username}", insights, extra)
        reports.write_pdf(out_dir, f"FULL:{username}", {**insights, **extra})
        if correos:
            reports.write_csv(out_dir, [{"email": e, "servicios": ";".join(s)} for e, s in correos.items()],
                              filename="correos_encontrados.csv")
        if breach_res and breach_res.get("breaches"):
            reports.write_csv(out_dir, breaches_csv_rows(breach_res), filename="brechas_detectadas.csv")

        summary = {
            "total": len(profiles),
            "alta": sum(1 for p in profiles if p["confianza"] == "ALTA"),
            "media": sum(1 for p in profiles if p["confianza"] == "MEDIA"),
            "baja": sum(1 for p in profiles if p["confianza"] == "BAJA"),
        }
        privacy = build_privacy_assessment(
            profiles=profiles, emails=correos, breaches=breach_res, signals=signals
        )
        graph = build_footprint_graph(
            username, profiles=profiles, emails=correos, phone=phone_res,
            breaches=breach_res, signals=signals,
        )
        _update_job(
            job_id,
            status="done",
            step="Pipeline completado",
            progress=100,
            finished_at=_now_iso(),
            files=_list_folder_files(out_dir),
            log=f"Pipeline finalizado: {summary['total']} perfiles, {len(correos)} correos.",
            result={
                "kind": "full",
                "username": username,
                "summary": summary,
                "profiles": profiles,
                "signals": signals,
                "emails": correos,
                "phone": phone_res,
                "breaches": breach_res,
                "privacy": privacy,
                "graph": graph,
            },
        )

    _run_in_background(job["id"], worker)
    return {"job_id": job["id"], "status": "running"}


@router.post("/api/breach/email")
def api_breach_email(req: BreachEmailRequest):
    _require_legitimate(req.confirm_legitimate)
    case_id = _validate_case_id(req.case_id)
    email = req.email.strip()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Correo electrónico inválido.")
    job = _create_job("breach", email, case_id=case_id)

    def worker(job_id: str):
        out_dir = utils.make_output_dir(f"breach_{email.replace('@', '_at_')}")
        _update_job(job_id, step="Consultando XposedOrNot y HIBP...", progress=35,
                    log=f"Verificando brechas para {email}...", folder=out_dir.name)
        res = check_email_breaches(email, out_dir=out_dir)
        secciones = format_breach_report(res)
        reports.write_html(out_dir, f"breach:{email}", secciones)
        reports.write_pdf(out_dir, f"breach:{email}", secciones)
        if res.get("breaches"):
            reports.write_csv(out_dir, breaches_csv_rows(res), filename="brechas_detectadas.csv")

        _update_job(
            job_id,
            status="done",
            step="Completado",
            progress=100,
            finished_at=_now_iso(),
            files=_list_folder_files(out_dir),
            log=f"Finalizado: {res['breach_count']} brechas encontradas.",
            result={"kind": "breach_email", "breaches": res},
        )

    _run_in_background(job["id"], worker)
    return {"job_id": job["id"], "status": "running"}


@router.post("/api/breach/password")
def api_breach_password(req: BreachPasswordRequest):
    """
    Audita una contraseña PROPIA por k-Anonymity.
    No escribe archivos, no crea job en memoria y nunca registra la contraseña.
    """
    return check_password_k_anonymity(req.password)


@router.post("/api/media/inspect")
async def api_media_inspect(file: UploadFile = File(...), case_id: Optional[str] = Form(default=None)):
    """
    Analiza metadatos de una imagen o video subido desde la interfaz web:
      - Extrae EXIF, GPS, XMP, códecs/streams (`ffprobe`) y hashes SHA-256/MD5.
      - Para imágenes genera además una copia limpia sin metadatos descargable.
      - Guarda reportes HTML, PDF y JSON en `output/<fecha>_media_<nombre>/`.
      - Si se indica `case_id`, vincula el archivo y su ejecución al caso en SQLite.
    """
    valid_case_id = _validate_case_id(case_id)
    raw_name = Path(file.filename or "archivo").name
    safe_stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in Path(raw_name).stem)[:45] or "media"
    ext = Path(raw_name).suffix.lower()
    out_dir = utils.make_output_dir(f"media_{safe_stem}")
    saved_path = out_dir / f"original_{safe_stem}{ext}"

    total = 0
    try:
        with saved_path.open("wb") as fh:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > _MAX_MEDIA_BYTES:
                    fh.close()
                    saved_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="El archivo supera el límite máximo de 150 MB.")
                fh.write(chunk)
    finally:
        await file.close()

    res = inspect_media(saved_path, original_name=raw_name, generate_clean_copy=True, out_dir=out_dir)

    (out_dir / "metadatos.json").write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
    secciones = format_media_report(res)
    reports.write_html(out_dir, f"media:{raw_name}", secciones)
    reports.write_pdf(out_dir, f"media:{raw_name}", secciones)

    files = _list_folder_files(out_dir)
    clean_url = f"/api/reports/{out_dir.name}/{res['clean_copy_name']}" if res.get("clean_copy_name") else None
    if valid_case_id:
        storage.add_media_item(
            valid_case_id,
            res,
            folder=out_dir.name,
            clean_copy_url=clean_url or "",
            local_path=str(saved_path),
        )
        storage.record_case_run(
            case_id=valid_case_id,
            kind="media",
            target=raw_name,
            status="done",
            folder=out_dir.name,
            summary={"media_type": res.get("kind"), "has_gps": bool(res.get("gps"))},
        )
    return {
        "folder": out_dir.name,
        "case_id": valid_case_id,
        "result": res,
        "clean_copy_url": clean_url,
        "files": files,
    }


@router.get("/api/locations/suggest")
def api_locations_suggest(q: str = "", kind: str = "city", country: str = "", remote: bool = True):
    """
    Devuelve sugerencias de autocompletado para ciudad/zona o país:
      1. Filtra primero el catálogo local (instantáneo, insensible a tildes).
      2. Si es búsqueda de ciudad con >= 3 caracteres y `remote=True`, complementa con
         OpenStreetMap Nominatim (gratis, sin API key y con caché en memoria) para
         cubrir municipios, barrios y zonas de todo el mundo.
    """
    qn = _norm_txt(q)
    cn = _norm_txt(country)

    if kind == "country":
        matches = [
            {"label": p["name"], "value": p["name"], "sub": p["code"], "country": p["name"]}
            for p in _PAISES_CATALOGO
            if not qn or qn in _norm_txt(p["name"]) or qn == p["code"].lower()
        ]
        return {"suggestions": matches[:10]}

    resultados = []
    vistos = set()
    for c in _CIUDADES_CATALOGO:
        if cn and cn not in _norm_txt(c["country"]):
            continue
        texto = _norm_txt(f"{c['city']} {c['region']} {c['country']}")
        if not qn or qn in texto:
            clave = f"{_norm_txt(c['city'])}|{_norm_txt(c['country'])}"
            if clave not in vistos:
                vistos.add(clave)
                resultados.append({
                    "label": f"{c['city']}, {c['region']}",
                    "value": c["city"],
                    "sub": c["country"],
                    "country": c["country"],
                })

    if remote and len(qn) >= 3 and len(resultados) < 6:
        cache_key = f"{qn}|{cn}"
        remotos = _LOC_CACHE.get(cache_key)
        if remotos is None:
            remotos = []
            try:
                from modules.http_client import cached_get_json

                query_str = f"{q.strip()}, {country.strip()}" if country.strip() else q.strip()
                res = cached_get_json(
                    source="nominatim_search",
                    url="https://nominatim.openstreetmap.org/search",
                    query_key=cache_key,
                    params={
                        "q": query_str,
                        "format": "json",
                        "addressdetails": 1,
                        "limit": 6,
                        "accept-language": "es",
                    },
                    headers={"User-Agent": "OSINT-Auto/2.0 (location-autocomplete)"},
                    timeout=4,
                    ttl_seconds=86400,
                    max_retries=1,
                )
                if res["ok"] and isinstance(res.get("data"), list):
                    for item in res["data"]:
                        addr = item.get("address") or {}
                        ciudad = (
                            addr.get("city")
                            or addr.get("town")
                            or addr.get("municipality")
                            or addr.get("village")
                            or addr.get("state")
                            or (item.get("display_name") or "").split(",")[0].strip()
                        )
                        region = addr.get("state") or addr.get("county") or ""
                        pais = addr.get("country") or ""
                        if not ciudad:
                            continue
                        remotos.append({
                            "label": f"{ciudad}, {region}" if region and region != ciudad else ciudad,
                            "value": ciudad,
                            "sub": pais or "OSM",
                            "country": pais,
                        })
            except Exception as exc:  # noqa: BLE001 - Nominatim es complemento opcional
                get_logger("routers.search").info("Autocompletado Nominatim no disponible para '%s': %s", q, exc)
                remotos = []
            if len(_LOC_CACHE) > 200:
                _LOC_CACHE.clear()
            _LOC_CACHE[cache_key] = remotos

        for r in remotos:
            clave = f"{_norm_txt(r['value'])}|{_norm_txt(r['country'])}"
            if clave not in vistos:
                vistos.add(clave)
                resultados.append(r)

    return {"suggestions": resultados[:10]}


@router.get("/api/jobs")
def api_jobs():
    """Devuelve los trabajos recientes (memoria + persistidos en SQLite) sin el payload completo."""
    persisted = {j["id"]: j for j in storage.list_persisted_jobs(limit=25)}
    with _JOBS_LOCK:
        for jid, j in _JOBS.items():
            persisted[jid] = {k: v for k, v in j.items() if k != "result"}
    ordered = sorted(persisted.values(), key=lambda x: x.get("started_at") or "", reverse=True)
    return {"jobs": ordered[:25]}


@router.get("/api/jobs/{job_id}")
def api_job_detail(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job:
            return dict(job)
    persisted = storage.get_persisted_job(job_id)
    if not persisted:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado.")
    return persisted


@router.get("/api/reports")
def api_reports():
    """Lista todas las carpetas generadas en `output/` con sus archivos descargables."""
    carpetas = []
    for d in utils.list_reports():
        partes = d.name.split("_")
        fecha = f"{partes[0]} {partes[1][:2]}:{partes[1][2:4]}:{partes[1][4:6]}" if len(partes) >= 2 and len(partes[1]) == 6 else d.name
        tipo = partes[2].upper() if len(partes) >= 3 else "OSINT"
        objetivo = "_".join(partes[3:]) if len(partes) >= 4 else d.name
        files = _list_folder_files(d)
        carpetas.append({
            "folder": d.name,
            "date": fecha,
            "type": tipo,
            "target": objetivo,
            "files": files,
            "has_html": any(f["name"] == "resumen.html" for f in files),
            "has_pdf": any(f["name"] == "reporte_final.pdf" for f in files),
        })
    return {"reports": carpetas}


@router.get("/api/reports/{folder}/{filename}")
def api_report_file(folder: str, filename: str):
    """
    Sirve un archivo dentro de `output/<folder>/<filename>` validando estrictamente
    que la ruta resuelta pertenezca a `OUTPUT_ROOT` (protección contra path traversal).
    """
    base = utils.OUTPUT_ROOT.resolve()
    target = (base / folder / filename).resolve()
    if base not in target.parents or not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    return FileResponse(str(target))
