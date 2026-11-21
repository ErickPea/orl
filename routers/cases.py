# -*- coding: utf-8 -*-
"""Router de Casos, Entidades, Conectores e Ingesta (Fases 1 y 2)."""
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from modules import storage
from modules.connectors import (
    connectors_catalog,
    fetch_from_connector,
    parse_imported_content,
    sample_import_template,
)
from routers.runtime import (
    _JOBS,
    _JOBS_LOCK,
    _create_job,
    _now_iso,
    _require_legitimate,
    _run_in_background,
    _update_job,
)
from routers.schemas import (
    CaseCreateRequest,
    CaseUpdateRequest,
    ConnectorIngestRequest,
    IdentifierCreateRequest,
    ProfileCreateRequest,
    ProfileStatusPatchRequest,
)

router = APIRouter()

_MAX_IMPORT_BYTES = 50 * 1024 * 1024  # 50 MB (soporta exportaciones ZIP de Meta)


@router.get("/api/cases")
def api_cases_list():
    """Lista todos los casos persistidos en SQLite con sus contadores."""
    return {"cases": storage.list_cases()}


@router.post("/api/cases", status_code=201)
def api_cases_create(req: CaseCreateRequest):
    """Crea un nuevo caso con su entidad principal y semillas iniciales opcionales."""
    detail = storage.create_case(
        title=req.title,
        subject_name=req.subject_name or "",
        purpose=req.purpose,
        notes=req.notes or "",
        seeds={
            "username": req.seed_username or "",
            "email": req.seed_email or "",
            "phone": req.seed_phone or "",
            "location": req.seed_location or "",
            "domain": req.seed_domain or "",
        },
    )
    return detail


@router.get("/api/cases/{case_id}")
def api_case_detail(case_id: str):
    """Devuelve toda la información consolidada del caso, perfiles y resolución de entidades."""
    detail = storage.get_case_detail(case_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return detail


@router.patch("/api/cases/{case_id}")
def api_case_update(case_id: str, req: CaseUpdateRequest):
    detail = storage.update_case(
        case_id=case_id,
        title=req.title,
        subject_name=req.subject_name,
        purpose=req.purpose,
        notes=req.notes,
        status=req.status,
        friend_scope_enabled=req.friend_scope_enabled,
    )
    if not detail:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return detail


@router.delete("/api/cases/{case_id}")
def api_case_delete(case_id: str, delete_files: bool = False):
    """Elimina un caso completo en SQLite (y opcionalmente sus carpetas asociadas en `output/`)."""
    res = storage.delete_case(case_id, delete_files=delete_files)
    if not res["deleted"]:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return res


@router.post("/api/cases/{case_id}/identifiers", status_code=201)
def api_case_add_identifier(case_id: str, req: IdentifierCreateRequest):
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    ident = storage.add_identifier(
        case_id=case_id,
        kind=req.kind,
        value=req.value,
        source=req.source,
        confidence=req.confidence,
        recalc=True,
    )
    return {"identifier": ident, "detail": storage.get_case_detail(case_id)}


@router.delete("/api/cases/{case_id}/identifiers/{identifier_id}")
def api_case_delete_identifier(case_id: str, identifier_id: int):
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    ok = storage.delete_identifier(case_id, identifier_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Identificador no encontrado.")
    return {"deleted": True, "detail": storage.get_case_detail(case_id)}


@router.post("/api/cases/{case_id}/profiles", status_code=201)
def api_case_add_profile(case_id: str, req: ProfileCreateRequest):
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    prof = storage.upsert_profile(
        case_id=case_id,
        site=req.site,
        url=req.url,
        username=req.username or "",
        display_name=req.display_name or "",
        bio=req.bio or "",
        location=req.location or "",
        avatar_url=req.avatar_url or "",
        website=req.website or "",
        sources=req.sources,
        osint_confidence=req.osint_confidence,
        recalc=True,
    )
    return {"profile": prof, "detail": storage.get_case_detail(case_id)}


@router.patch("/api/cases/{case_id}/profiles/{profile_id}")
def api_case_patch_profile(case_id: str, profile_id: int, req: ProfileStatusPatchRequest):
    """Permite confirmar o descartar manualmente un perfil (control humano sobre homónimos)."""
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    res = storage.set_profile_manual_status(case_id, profile_id, req.manual_status)
    if res is None:
        raise HTTPException(status_code=404, detail="Perfil no encontrado en el caso.")
    return {"resolution": res, "detail": storage.get_case_detail(case_id)}


@router.post("/api/cases/{case_id}/resolve")
def api_case_resolve(case_id: str):
    """Recalcula el motor de Resolución de Entidades sobre todos los perfiles del caso."""
    res = storage.recalculate_case_resolution(case_id)
    if res is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return {"resolution": res, "detail": storage.get_case_detail(case_id)}


@router.post("/api/cases/{case_id}/import-job/{job_id}")
def api_case_import_job(case_id: str, job_id: str):
    """Adjunta e ingiere los resultados de un job ya ejecutado dentro de un caso existente."""
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    with _JOBS_LOCK:
        job = dict(_JOBS[job_id]) if job_id in _JOBS else None
    if not job:
        job = storage.get_persisted_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado.")
    if job.get("status") != "done" or not isinstance(job.get("result"), dict):
        raise HTTPException(status_code=400, detail="El trabajo aún no ha finalizado o no tiene resultados estructurados.")
    ing = storage.ingest_job_result_to_case(
        case_id=case_id,
        job_id=job["id"],
        kind=job["kind"],
        target=job["target"],
        folder=job.get("folder") or "",
        result=job["result"],
    )
    return {"ingestion": ing, "detail": storage.get_case_detail(case_id)}


@router.get("/api/connectors")
def api_connectors_list():
    """Devuelve el catálogo de conectores oficiales disponibles y su estado de configuración."""
    return {"connectors": connectors_catalog()}


@router.get("/api/ingest/template")
def api_ingest_template():
    """Devuelve una plantilla JSON de ejemplo para el importador de exportaciones propias."""
    return sample_import_template()


@router.post("/api/cases/{case_id}/ingest/connector")
def api_case_ingest_connector(case_id: str, req: ConnectorIngestRequest):
    """
    Ejecuta en segundo plano un conector oficial de API abierta (Bluesky, Mastodon, Reddit,
    GitHub o YouTube) y persiste bio, publicaciones, fotos públicas, comentarios e interacciones
    directamente en SQLite del caso.
    """
    _require_legitimate(req.confirm_legitimate)
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")

    platform = req.platform.strip().lower()
    handle = req.handle.strip()
    job = _create_job(f"ingest:{platform}", handle, case_id=case_id)

    def worker(job_id: str):
        _update_job(
            job_id,
            step=f"Consultando API pública de {platform} para '{handle}'...",
            progress=25,
            log=f"Conectando con API abierta oficial ({platform})...",
        )
        bundle = fetch_from_connector(
            platform=platform,
            handle=handle,
            limit=req.limit,
            include_replies=req.include_replies,
        )
        _update_job(
            job_id,
            step="Guardando publicaciones, media, comentarios e interacciones en SQLite...",
            progress=75,
            log=(
                f"Recibidos: {len(bundle['posts'])} posts, {len(bundle['media_items'])} media, "
                f"{len(bundle['comments'])} comentarios, {len(bundle['interactions'])} interacciones."
            ),
        )
        summary = storage.ingest_connector_bundle(case_id=case_id, bundle=bundle, job_id=job_id)
        warn_msg = f" Avisos: {'; '.join(bundle['warnings'])}" if bundle.get("warnings") else ""
        _update_job(
            job_id,
            status="done",
            step="Ingesta completada",
            progress=100,
            finished_at=_now_iso(),
            log=f"Guardado en caso {case_id}: {summary['posts_inserted']} posts nuevos, "
                f"{summary['media_inserted']} fotos, {summary['comments_inserted']} comentarios.{warn_msg}",
            result={"kind": f"ingest:{platform}", "summary": summary, "warnings": bundle.get("warnings") or []},
        )

    _run_in_background(job["id"], worker)
    return {"job_id": job["id"], "status": "running"}


@router.post("/api/cases/{case_id}/ingest/import")
async def api_case_ingest_import(
    case_id: str,
    file: UploadFile = File(...),
    platform_label: str = Form(default="exportacion_propia"),
    subject_handle: str = Form(default=""),
):
    """
    Importa un archivo JSON o CSV (exportación propia o dataset autorizado) y lo guarda
    normalizado en `posts`, `comments`, `media_items` e `interactions` del caso.
    """
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")

    raw_name = Path(file.filename or "import.json").name
    ext = Path(raw_name).suffix.lower()
    if ext not in (".json", ".csv", ".zip", ".txt"):
        await file.close()
        raise HTTPException(
            status_code=400,
            detail="Formatos admitidos: .json, .csv, .zip (exportación Meta/Facebook/Instagram) o .txt (chat WhatsApp).",
        )

    chunks = []
    total = 0
    try:
        while True:
            chunk = await file.read(512 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_IMPORT_BYTES:
                raise HTTPException(status_code=413, detail="El archivo supera el límite máximo de 20 MB.")
            chunks.append(chunk)
    finally:
        await file.close()

    try:
        bundle = parse_imported_content(
            raw_bytes=b"".join(chunks),
            filename=raw_name,
            platform_label=platform_label,
            subject_handle=subject_handle or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    summary = storage.ingest_connector_bundle(case_id=case_id, bundle=bundle)
    return {
        "summary": summary,
        "detail": storage.get_case_detail(case_id),
        "content": storage.get_case_content(case_id),
    }


@router.get("/api/cases/{case_id}/content")
def api_case_content(case_id: str, limit_posts: int = 50, limit_comments: int = 100, limit_media: int = 60):
    """Devuelve publicaciones, comentarios, fotos/media e interacciones ingeridas de un caso."""
    content = storage.get_case_content(
        case_id=case_id,
        limit_posts=max(1, min(limit_posts, 200)),
        limit_comments=max(1, min(limit_comments, 300)),
        limit_media=max(1, min(limit_media, 150)),
    )
    if content is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return content
