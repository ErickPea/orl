# -*- coding: utf-8 -*-
"""Router de Análisis (Visión, NLP, SNA, Geo-Historial, Perfil del Sujeto, Reportes y Mantenimiento)."""
import csv
import io
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse

from modules import storage, utils
from modules.logger import get_logger
from modules.case_report import (
    cleanup_orphan_outputs,
    compute_case_exposure,
    generate_case_report,
    storage_maintenance_summary,
)
from modules.media_meta import inspect_media
from modules.text_analysis import analyze_case_nlp, nlp_capabilities
from modules.vision_analysis import analyze_image_bytes, download_public_image, vision_capabilities
from routers.runtime import _create_job, _now_iso, _run_in_background, _update_job
from routers.schemas import (
    CaseReportRequest,
    CleanupRequest,
    GeoHintsRecomputeRequest,
    ManualInteractionRequest,
    MediaGeoInvestigateRequest,
    NlpAnalyzeRequest,
    SubjectProfileReviewRequest,
    VisionAnalyzeRequest,
)

router = APIRouter()

_GALLERY_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
_MAX_GALLERY_IMAGE_BYTES = 25 * 1024 * 1024  # 25 MB por foto


# ---------------------------------------------------------------------------
# Fase 3 :: Galería Local y Análisis Visual Multimodal (dHash + VLM + OCR)
# ---------------------------------------------------------------------------
@router.get("/api/vision/status")
def api_vision_status():
    """Devuelve disponibilidad de motores de visión (Pillow/dHash, Ollama, Gemini, OpenAI, Tesseract)."""
    return vision_capabilities(probe_ollama=True)


@router.post("/api/cases/{case_id}/media/upload")
async def api_case_media_upload(
    case_id: str,
    files: List[UploadFile] = File(...),
    alt_text: str = Form(default=""),
):
    """
    Sube una o varias fotos locales a la galería del caso (guardadas en `output/case_<id>_gallery/`),
    calcula hashes + metadatos EXIF básicos y las deja listas para análisis visual.
    """
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")

    folder_name = f"case_{case_id}_gallery"
    gallery_dir = utils.OUTPUT_ROOT / folder_name
    gallery_dir.mkdir(parents=True, exist_ok=True)

    uploaded = []
    skipped = []

    for f in files:
        raw_name = Path(f.filename or "foto.jpg").name
        ext = Path(raw_name).suffix.lower() or ".jpg"
        if ext not in _GALLERY_EXTS:
            await f.close()
            skipped.append(f"{raw_name}: formato no soportado")
            continue

        safe_stem = "".join(c if c.isalnum() or c in "-_." else "_" for c in Path(raw_name).stem)[:45] or "foto"
        uniq_name = f"{uuid.uuid4().hex[:8]}_{safe_stem}{ext}"
        dest_path = gallery_dir / uniq_name

        total = 0
        try:
            with dest_path.open("wb") as out_fh:
                while True:
                    chunk = await f.read(512 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_GALLERY_IMAGE_BYTES:
                        out_fh.close()
                        dest_path.unlink(missing_ok=True)
                        skipped.append(f"{raw_name}: supera 25 MB")
                        total = -1
                        break
                    out_fh.write(chunk)
        finally:
            await f.close()

        if total < 0:
            continue

        meta = inspect_media(dest_path, original_name=raw_name, generate_clean_copy=False, out_dir=gallery_dir)
        hashes = meta.get("hashes") or {}
        gps = meta.get("gps") or {}
        public_url = f"/api/reports/{folder_name}/{uniq_name}"
        item = storage.add_case_gallery_image(
            case_id=case_id,
            filename=raw_name,
            local_path=str(dest_path),
            public_url=public_url,
            folder=folder_name,
            sha256=hashes.get("sha256") or "",
            md5=hashes.get("md5") or "",
            file_size=total,
            alt_text=alt_text,
            gps_lat=gps.get("latitude"),
            gps_lon=gps.get("longitude"),
            captured_date=meta.get("date_taken") or "",
            device=meta.get("device") or meta.get("camera") or "",
            metadata={"exif_summary": meta.get("privacy") or {}},
        )
        uploaded.append(item)

    if uploaded:
        storage.record_case_run(
            case_id=case_id,
            kind="media:upload",
            target=f"{len(uploaded)} foto(s)",
            status="done",
            folder=folder_name,
            summary={"uploaded": len(uploaded), "skipped": len(skipped)},
        )

    return {
        "uploaded": uploaded,
        "skipped": skipped,
        "vision": storage.get_case_vision_summary(case_id),
    }


@router.post("/api/cases/{case_id}/vision/analyze")
def api_case_vision_analyze(case_id: str, req: VisionAnalyzeRequest):
    """
    Job asíncrono de Fase 3: analiza las fotos de la galería del caso calculando dHash
    perceptual, paleta, OCR y temas visuales (usando VLM si está disponible o motor local).
    """
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")

    job = _create_job(f"vision:{req.provider}", f"Galería caso {case_id}", case_id=case_id)

    def worker(job_id: str):
        items = storage.get_case_media_for_vision(case_id, only_pending=req.only_pending)[: req.max_images]
        total = len(items)
        if total == 0:
            summary = storage.get_case_vision_summary(case_id)
            _update_job(
                job_id,
                status="done",
                step="Sin imágenes pendientes por analizar",
                progress=100,
                finished_at=_now_iso(),
                log="No hay imágenes pendientes en este caso.",
                result={"kind": "vision", "analyzed": 0, "vision": summary},
            )
            return

        _update_job(
            job_id,
            step=f"Analizando {total} foto(s) con motor '{req.provider}'...",
            progress=10,
            log=f"Iniciando análisis visual sobre {total} imágenes...",
        )

        analyzed = 0
        for idx, m in enumerate(items, start=1):
            img_bytes = b""
            mime_type = "image/jpeg"
            lpath = m.get("local_path") or ""
            if lpath and Path(lpath).is_file():
                try:
                    img_bytes = Path(lpath).read_bytes()
                    ext = Path(lpath).suffix.lower()
                    if ext == ".png":
                        mime_type = "image/png"
                    elif ext == ".webp":
                        mime_type = "image/webp"
                except Exception as exc:  # noqa: BLE001
                    get_logger("routers.analysis").warning("No se pudo leer archivo local %s: %s", lpath, exc)
                    img_bytes = b""
            elif (m.get("source_url") or "").lower().startswith(("http://", "https://")):
                img_bytes, mime_type = download_public_image(m["source_url"])

            v_res = analyze_image_bytes(
                image_bytes=img_bytes,
                alt_text=m.get("alt_text") or "",
                caption=m.get("caption") or "",
                filename=m.get("filename") or "",
                provider=req.provider,
                mime_type=mime_type,
            )
            storage.update_media_vision(case_id, m["id"], v_res)
            analyzed += 1

            pct = 10 + int((idx / total) * 85)
            _update_job(
                job_id,
                step=f"Analizada {idx}/{total}: {m.get('filename') or 'imagen'}",
                progress=pct,
                log=f"[{idx}/{total}] {m.get('filename')}: motor={v_res['engine']}, "
                    f"temas={len(v_res['themes'])}, dHash={v_res['dhash'] or 'n/a'}",
            )

        summary = storage.get_case_vision_summary(case_id)
        storage.record_case_run(
            case_id=case_id,
            job_id=job_id,
            kind=f"vision:{req.provider}",
            target=f"{analyzed} foto(s)",
            status="done",
            folder="",
            summary=summary["stats"],
        )
        _update_job(
            job_id,
            status="done",
            step="Análisis visual completado",
            progress=100,
            finished_at=_now_iso(),
            log=f"Finalizado: {analyzed} fotos analizadas, {summary['stats']['themes_detected']} temas visuales, "
                f"{summary['stats']['similar_pairs']} pares similares por dHash.",
            result={"kind": "vision", "analyzed": analyzed, "vision": summary},
        )

    _run_in_background(job["id"], worker)
    return {"job_id": job["id"], "status": "running"}


@router.get("/api/cases/{case_id}/vision")
def api_case_vision_summary(case_id: str):
    """Devuelve galería etiquetada, intereses visuales con evidencia y duplicados por dHash."""
    summary = storage.get_case_vision_summary(case_id)
    if summary is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return summary


# ---------------------------------------------------------------------------
# Fase 4 :: NLP sobre Biografías, Publicaciones y Comentarios
# ---------------------------------------------------------------------------
@router.get("/api/nlp/status")
def api_nlp_status():
    """Devuelve disponibilidad de motores NLP (léxico local, Ollama, Gemini, OpenAI)."""
    return nlp_capabilities(probe_ollama=True)


@router.post("/api/cases/{case_id}/nlp/analyze")
def api_case_nlp_analyze(case_id: str, req: NlpAnalyzeRequest):
    """
    Job asíncrono de Fase 4: analiza biografías, publicaciones y comentarios del caso,
    fusiona temas de texto con los temas visuales de Fase 3, extrae entidades (NER),
    evalúa sentimiento separando sujeto vs. terceros y calcula patrones temporales.
    """
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")

    job = _create_job(f"nlp:{req.provider}", f"Corpus NLP caso {case_id}", case_id=case_id)

    def worker(job_id: str):
        _update_job(
            job_id,
            step="Recopilando biografías, publicaciones, comentarios y temas visuales...",
            progress=20,
            log=f"Preparando corpus NLP del caso {case_id}...",
        )
        corpus = storage.get_case_corpus_for_nlp(case_id)
        _update_job(
            job_id,
            step=f"Analizando intereses, NER y sentimiento ({req.provider})...",
            progress=55,
            log=(
                f"Corpus: {len(corpus['bios'])} bios, {len(corpus['posts'])} posts, "
                f"{len(corpus['comments'])} comentarios, {len(corpus['vision_themes'])} temas visuales."
            ),
        )
        nlp_res = analyze_case_nlp(corpus, provider=req.provider)
        storage.save_case_nlp_analysis(case_id, nlp_res)
        storage.record_case_run(
            case_id=case_id,
            job_id=job_id,
            kind=f"nlp:{nlp_res['engine']}",
            target=f"{nlp_res['counts']['subject_docs']} textos + {nlp_res['counts']['others_docs']} terceros",
            status="done",
            folder="",
            summary=nlp_res["counts"],
        )
        saved = storage.get_case_nlp_analysis(case_id)
        _update_job(
            job_id,
            status="done",
            step="Análisis NLP completado",
            progress=100,
            finished_at=_now_iso(),
            log=f"Finalizado ({nlp_res['engine']}): {nlp_res['counts']['interests_count']} intereses detectados, "
                f"{nlp_res['counts']['comments_analyzed']} comentarios etiquetados con sentimiento.",
            result={"kind": "nlp", "nlp": saved},
        )

    _run_in_background(job["id"], worker)
    return {"job_id": job["id"], "status": "running"}


@router.get("/api/cases/{case_id}/nlp")
def api_case_nlp_get(case_id: str):
    """Devuelve el análisis NLP consolidado del caso."""
    data = storage.get_case_nlp_analysis(case_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return data


# ---------------------------------------------------------------------------
# Fase 5 :: Grafo de Interacciones y Círculo Cercano (SNA)
# ---------------------------------------------------------------------------
@router.get("/api/cases/{case_id}/sna")
def api_case_sna(case_id: str):
    """Calcula y devuelve el Grafo SNA, índice de cercanía, comunidades y perfil de relaciones."""
    data = storage.compute_and_save_case_sna(case_id, record_run=False)
    if data is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return data


@router.post("/api/cases/{case_id}/sna/recompute")
def api_case_sna_recompute(case_id: str):
    """Recalcula el Grafo SNA del caso y registra la ejecución en la bitácora."""
    data = storage.compute_and_save_case_sna(case_id, record_run=True)
    if data is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return data


@router.post("/api/cases/{case_id}/sna/interaction", status_code=201)
def api_case_sna_add_interaction(case_id: str, req: ManualInteractionRequest):
    """
    Registra manualmente una interacción o comentario observado (por ejemplo al revisar
    manualmente tu perfil en Facebook/Instagram/Threads) y recalcula el Grafo SNA al instante.
    """
    inputs = storage.get_case_sna_inputs(case_id)
    if inputs is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")

    subj = (inputs["subject_handles"][0] if inputs.get("subject_handles") else (inputs.get("subject_name") or "sujeto")).strip().lstrip("@").lower()
    peer = req.peer_handle.strip().lstrip("@").lower()
    if not peer or peer == subj:
        raise HTTPException(status_code=400, detail="Indica un usuario o contacto distinto del sujeto principal.")

    plat = (req.platform or "manual").strip().lower()
    pub = (req.published_at or "").strip() or _now_iso()
    content = (req.content or "").strip()

    from modules.connectors.common import add_interaction, empty_bundle

    bundle = empty_bundle(plat, subj)
    uid = uuid.uuid4().hex[:8]
    if req.direction in ("incoming", "mutual"):
        add_interaction(bundle["interactions"], peer, subj, req.kind, weight=1.6, metadata={"platform": plat, "manual": True})
        if content:
            bundle["comments"].append({
                "external_id": f"man_in_{uid}",
                "post_external_id": "",
                "author_handle": peer,
                "target_handle": subj,
                "content": content,
                "published_at": pub,
                "metadata": {"platform": plat, "manual": True},
            })
    if req.direction in ("outgoing", "mutual"):
        add_interaction(bundle["interactions"], subj, peer, req.kind, weight=1.6, metadata={"platform": plat, "manual": True})
        if content and req.direction == "outgoing":
            bundle["comments"].append({
                "external_id": f"man_out_{uid}",
                "post_external_id": "",
                "author_handle": subj,
                "target_handle": peer,
                "content": content,
                "published_at": pub,
                "metadata": {"platform": plat, "manual": True},
            })

    storage.ingest_connector_bundle(case_id=case_id, bundle=bundle)
    return storage.compute_and_save_case_sna(case_id, record_run=False)


# ---------------------------------------------------------------------------
# Fase 6 :: Reporte Integral (HTML/PDF/JSON), Exposición y Gobernanza
# ---------------------------------------------------------------------------
@router.get("/api/cases/{case_id}/exposure")
def api_case_exposure(case_id: str):
    """Evalúa el nivel defensivo de exposición pública del caso consolidando Fases 1–5."""
    detail = storage.get_case_detail(case_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    vision = storage.get_case_vision_summary(case_id) or {}
    nlp = storage.get_case_nlp_analysis(case_id) or {}
    sna = storage.compute_and_save_case_sna(case_id, record_run=False) or {}
    return compute_case_exposure(detail, vision=vision, nlp=nlp, sna=sna)


@router.post("/api/cases/{case_id}/report")
def api_case_generate_report(case_id: str, req: CaseReportRequest):
    """
    Genera el Reporte Integral del Caso (HTML + PDF + JSON) en `output/` con soporte
    de anonimización opcional de terceros.
    """
    res = generate_case_report(
        case_id=case_id,
        anonymize=req.anonymize_third_parties,
        include_quotes=req.include_quotes,
        include_gallery=req.include_gallery,
        reveal_confirmed_friends=req.reveal_confirmed_friends,
    )
    if res is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return res


@router.get("/api/maintenance/storage")
def api_maintenance_storage():
    """Devuelve estadísticas de almacenamiento de `output/` y la base SQLite."""
    return storage_maintenance_summary()


@router.post("/api/maintenance/cleanup")
def api_maintenance_cleanup(req: CleanupRequest):
    """Limpia carpetas huérfanas en `output/` que no pertenecen a ningún caso activo."""
    return cleanup_orphan_outputs(dry_run=req.dry_run)


# ---------------------------------------------------------------------------
# Geolocalización Visual + Historial de Ubicaciones + Perfil del Sujeto
# ---------------------------------------------------------------------------
@router.get("/api/cases/{case_id}/geo-hints")
def api_case_geo_hints(case_id: str):
    """Devuelve pistas de ubicación visual (`geo_hints`) y ranking probabilístico del caso."""
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    from modules.geo_inference import infer_case_locations

    hints = storage.list_case_geo_hints(case_id)
    content = storage.get_case_content(case_id)
    nlp_data = storage.get_case_nlp_analysis(case_id) or {}
    inferred = infer_case_locations(
        geo_hints=hints,
        media_items=content.get("media_items") or [],
        posts=content.get("posts") or [],
        nlp_data=nlp_data,
    )
    return {"case_id": case_id, "hints": hints, **inferred}


@router.post("/api/cases/{case_id}/geo-hints/recompute")
def api_case_geo_hints_recompute(case_id: str, req: GeoHintsRecomputeRequest = GeoHintsRecomputeRequest()):
    """Recalcula `geo_hints` por imagen y fusiona con EXIF + NER en ranking probabilístico."""
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    from modules.geo_inference import extract_media_gps, infer_case_locations
    from modules.vision_analysis import extract_geo_hints

    content = storage.get_case_content(case_id)
    media_items = content.get("media_items") or []
    posts = content.get("posts") or []
    post_caption_by_id = {p.get("id"): (p.get("content") or "") for p in posts}
    nlp_data = storage.get_case_nlp_analysis(case_id) or {}

    all_hints = []
    for m in media_items:
        gps = extract_media_gps(m)
        cap = post_caption_by_id.get(m.get("post_id"), "")
        lp = Path(m["local_path"]) if m.get("local_path") else None
        extracted = extract_geo_hints(
            image_path=lp if (lp and lp.is_file()) else None,
            ocr_text=m.get("ocr_text") or "",
            alt_text=m.get("alt_text") or "",
            caption=cap,
            exif_gps=gps,
        )
        saved = storage.save_media_geo_hints(case_id, m["id"], extracted.get("hints") or [])
        all_hints.extend(saved)

    stored_hints = storage.list_case_geo_hints(case_id)
    inferred = infer_case_locations(
        geo_hints=stored_hints,
        media_items=media_items,
        posts=posts,
        nlp_data=nlp_data,
        use_nominatim=req.use_nominatim,
    )
    return {"case_id": case_id, "hints": stored_hints, "recomputed_count": len(all_hints), **inferred}


@router.post("/api/cases/{case_id}/media/{media_id}/geo-investigate")
def api_case_media_geo_investigate(case_id: str, media_id: int, req: MediaGeoInvestigateRequest = MediaGeoInvestigateRequest()):
    """Investiga ubicación de UNA sola foto o fija ubicación manual persistente."""
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    m = storage.get_media_item(case_id, media_id)
    if not m:
        raise HTTPException(status_code=404, detail="Media no encontrado.")

    owner_type = m.get("owner_type") or "subject"
    owner_id = m.get("owner_id")
    pub_at = m.get("published_at") or m.get("captured_date") or m.get("created_at") or ""

    if req.manual_override:
        loc_rec = storage.save_location_history_entry(
            case_id=case_id,
            media_id=media_id,
            owner_type=owner_type,
            owner_id=owner_id,
            source="manual",
            country=req.country,
            region=req.region,
            city=req.city,
            lat=req.lat,
            lon=req.lon,
            confidence=1.0,
            evidence_quote=req.evidence_quote or "Ubicación confirmada manualmente por el analista",
            published_at=pub_at,
            is_manual_override=True,
        )
        return {"case_id": case_id, "media_id": media_id, "manual_override": True, "location": loc_rec, "candidates": []}

    from modules.geo_inference import extract_media_gps, infer_case_locations
    from modules.vision_analysis import build_geo_vlm_client, extract_geo_hints

    meta = m.get("metadata") or {}
    gps = extract_media_gps(m)
    lp = Path(m["local_path"]) if m.get("local_path") else None
    post_cap = ""
    if m.get("post_id"):
        c_data = storage.get_case_content(case_id, limit_posts=100) or {}
        for p in c_data.get("posts") or []:
            if p.get("id") == m.get("post_id"):
                post_cap = p.get("content") or ""
                break
    vlm_cb = build_geo_vlm_client(lp, m.get("source_url") or "") if req.use_vlm else None
    extracted = extract_geo_hints(
        image_path=lp if (lp and lp.is_file()) else None,
        vlm_client=vlm_cb,
        ocr_text=m.get("ocr_text") or "",
        alt_text=m.get("alt_text") or "",
        caption=post_cap or meta.get("caption") or "",
        exif_gps=gps,
    )
    hints = extracted.get("hints") or []
    storage.save_media_geo_hints(case_id, media_id, hints)

    inferred = infer_case_locations(
        geo_hints=hints,
        media_items=[m],
        posts=[],
        nlp_data={},
        use_nominatim=req.use_nominatim,
    )
    cands = inferred.get("candidates") or []
    saved_loc = None
    if cands:
        top = cands[0]
        saved_loc = storage.save_location_history_entry(
            case_id=case_id,
            media_id=media_id,
            owner_type=owner_type,
            owner_id=owner_id,
            source=top.get("source") or "local_context",
            country=top.get("country") or "",
            region=top.get("region") or "",
            city=top.get("city") or "",
            lat=top.get("lat"),
            lon=top.get("lon"),
            confidence=float(top.get("confidence") or 0.5),
            evidence_quote="; ".join(top.get("evidence") or [])[:380],
            published_at=pub_at,
            is_manual_override=False,
        )
    return {
        "case_id": case_id,
        "media_id": media_id,
        "manual_override": False,
        "geo_hints": extracted,
        "location": saved_loc,
        **inferred,
    }


@router.get("/api/cases/{case_id}/location-history")
def api_case_location_history(
    case_id: str,
    date_from: str = "",
    date_to: str = "",
    min_confidence: float = 0.0,
    source: str = "",
    format: str = "json",
    request: Request = None,
):
    """Historial cronológico de ubicaciones del sujeto (ordenable y exportable a CSV)."""
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    if request is not None:
        date_from = request.query_params.get("from", date_from) or ""
        date_to = request.query_params.get("to", date_to) or ""
    rows = storage.list_location_history(
        case_id=case_id,
        owner_type="subject",
        date_from=date_from,
        date_to=date_to,
        min_confidence=min_confidence,
        source=source,
    )
    if format.lower() == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "media_id", "published_at", "country", "region", "city", "lat", "lon", "confidence", "source", "is_manual_override", "evidence_quote"])
        for r in rows:
            writer.writerow([
                r.get("id"), r.get("media_id"), r.get("sort_date") or r.get("published_at"),
                r.get("country"), r.get("region"), r.get("city"),
                r.get("lat"), r.get("lon"), r.get("confidence"),
                r.get("source"), r.get("is_manual_override"), r.get("evidence_quote"),
            ])
        return HTMLResponse(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="location_history_{case_id}.csv"'},
        )
    return {"case_id": case_id, "count": len(rows), "history": rows}


@router.get("/api/cases/{case_id}/subject-profile")
def api_case_subject_profile(case_id: str):
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    entries = storage.list_subject_profile(case_id)
    by_cat = {}
    for e in entries:
        by_cat.setdefault(e["category"], []).append(e)
    return {"case_id": case_id, "count": len(entries), "entries": entries, "by_category": by_cat}


@router.post("/api/cases/{case_id}/subject-profile/recompute")
def api_case_subject_profile_recompute(case_id: str):
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    from modules.subject_profile import compute_subject_profile
    return compute_subject_profile(case_id, persist=True)


@router.patch("/api/cases/{case_id}/subject-profile/{entry_id}")
def api_case_subject_profile_review(case_id: str, entry_id: int, req: SubjectProfileReviewRequest):
    updated = storage.update_subject_profile_review(case_id, entry_id, req.human_review)
    if not updated:
        raise HTTPException(status_code=404, detail="Entrada de perfil no encontrada.")
    return {"case_id": case_id, "entry": updated}
