# -*- coding: utf-8 -*-
"""Router de OAuth2, Investigación de Cuentas, Lenguaje Relacional, Agente Navegador y Amigos."""
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from modules import browser_agent, storage
from modules.account_investigator import investigate_social_account, run_case_relational_analysis
from modules.browser_agent import (
    clean_browser_cache,
    clear_platform_session,
    commit_staged_run_to_case,
    get_run_control,
    open_interactive_login_window,
    playwright_available,
    session_status,
    start_browser_investigation,
)
from modules.connectors import platforms_capabilities_catalog
from modules.platform_auth import complete_oauth_callback, oauth_provider_status, start_oauth_flow
from routers.runtime import _create_job, _now_iso, _require_legitimate, _run_in_background, _update_job
from routers.schemas import (
    AccountInvestigateRequest,
    AgentCommitRequest,
    BrowserAgentStartRequest,
    BrowserCacheCleanRequest,
    BrowserSessionOpenRequest,
    FriendAddRequest,
    FriendInvestigateRequest,
    FriendLinkSnaRequest,
    FriendScanStartRequest,
    RelationalAnalyzeRequest,
    RelationalReviewPatchRequest,
    StagedItemPatchRequest,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# OAuth2, Investigación Autorizada de Cuentas, Evidencias y Lenguaje Relacional
# ---------------------------------------------------------------------------
@router.get("/api/platforms/capabilities")
def api_platforms_capabilities():
    """Devuelve la matriz honesta de capacidades reales por plataforma y conexiones OAuth2 activas."""
    conns = storage.list_platform_connections()
    return {
        "platforms": platforms_capabilities_catalog(active_connections=conns),
        "oauth_providers": oauth_provider_status(),
    }


@router.get("/api/auth/connections")
def api_auth_connections():
    """Lista las conexiones OAuth2 guardadas (nunca expone tokens cifrados ni secretos)."""
    return {
        "connections": storage.list_platform_connections(),
        "oauth_providers": oauth_provider_status(),
    }


@router.get("/api/auth/{platform}/start")
def api_auth_oauth_start(platform: str, request: Request):
    """Inicia flujo oficial OAuth2 (PKCE + anti-CSRF state) devolviendo la URL de autorización."""
    base_url = str(request.base_url).rstrip("/")
    try:
        return start_oauth_flow(platform=platform, base_callback_url=base_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/auth/{platform}/callback", response_class=HTMLResponse)
def api_auth_oauth_callback(platform: str, code: str = "", state: str = "", error: str = ""):
    """Recibe el callback OAuth2 oficial, guarda el token cifrado y notifica a la ventana principal."""
    if error:
        return HTMLResponse(
            f"<html><body style='font-family:sans-serif;background:#0f172a;color:#f8fafc;padding:24px'>"
            f"<h3>Autorización cancelada ({platform})</h3><p>{error}</p>"
            f"<p><a href='/' style='color:#38bdf8'>Volver a OSINT-Auto</a></p></body></html>",
            status_code=400,
        )
    if not code or not state:
        raise HTTPException(status_code=400, detail="Faltan parámetros OAuth2 (`code` y `state`).")
    try:
        conn = complete_oauth_callback(platform=platform, code=code, state=state)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return HTMLResponse(
        "<html><body style='font-family:sans-serif;background:#0f172a;color:#f8fafc;padding:24px'>"
        f"<h3>Conexión autorizada: {conn.get('platform')} ({conn.get('account_label')})</h3>"
        "<p>El token OAuth2 quedó guardado y cifrado localmente. Puedes cerrar esta pestaña y recargar OSINT-Auto.</p>"
        "<p><a href='/' style='color:#38bdf8'>Volver a OSINT-Auto</a></p></body></html>"
    )


@router.delete("/api/auth/connections/{conn_id}")
def api_auth_delete_connection(conn_id: str):
    """Revoca y elimina localmente una conexión OAuth2."""
    ok = storage.delete_platform_connection(conn_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Conexión no encontrada.")
    return {"deleted": True}


@router.post("/api/cases/{case_id}/investigate/account")
def api_case_investigate_account(case_id: str, req: AccountInvestigateRequest):
    """
    Lanza una investigación integral autorizada sobre una cuenta seleccionada:
    extrae perfil + posts + media + comentarios + reacciones -> guarda snapshots y evidencias ->
    ejecuta lenguaje relacional + NLP + SNA.
    """
    _require_legitimate(req.confirm_legitimate)
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")

    plat = req.platform.strip().lower()
    handle = req.handle.strip()
    job = _create_job(f"investigate:{plat}", handle, case_id=case_id)

    def worker(job_id: str):
        def _cb(step: str, pct: int, log: str):
            _update_job(job_id, step=step, progress=pct, log=log)

        res = investigate_social_account(
            case_id=case_id,
            platform=plat,
            handle=handle,
            limit=req.limit,
            include_replies=req.include_replies,
            include_reactions=req.include_reactions,
            connection_id=req.connection_id,
            run_relational=req.run_relational,
            relational_provider=req.relational_provider,
            run_nlp=req.run_nlp,
            nlp_provider=req.nlp_provider,
            run_sna=req.run_sna,
            job_id=job_id,
            progress_cb=_cb,
        )
        ing = res.get("ingest") or {}
        rel = res.get("relational") or {}
        warns = res.get("warnings") or []
        warn_txt = f" Avisos: {'; '.join(warns)}" if warns else ""
        _update_job(
            job_id,
            status="done",
            step="Investigación de cuenta completada",
            progress=100,
            finished_at=_now_iso(),
            log=(
                f"Finalizado ({plat}): {ing.get('posts_inserted', 0)} posts, "
                f"{ing.get('comments_inserted', 0)} comentarios, {ing.get('reactions_inserted', 0)} reacciones, "
                f"{rel.get('total', 0)} señales relacionales.{warn_txt}"
            ),
            result={"kind": "account_investigation", **res},
        )

    _run_in_background(job["id"], worker)
    return {"job_id": job["id"], "status": "running"}


@router.get("/api/cases/{case_id}/relational")
def api_case_relational_list(
    case_id: str,
    category: Optional[str] = None,
    epistemic_type: Optional[str] = None,
    review_status: Optional[str] = None,
    handle: Optional[str] = None,
):
    """Lista las señales de lenguaje relacional del caso con trazabilidad y filtros."""
    data = storage.list_relational_signals(
        case_id=case_id,
        category=category,
        epistemic_type=epistemic_type,
        review_status=review_status,
        handle=handle,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return data


@router.post("/api/cases/{case_id}/relational/analyze")
def api_case_relational_analyze(case_id: str, req: RelationalAnalyzeRequest):
    """Ejecuta (o re-ejecuta) el análisis de lenguaje relacional en modo local o asistido por LLM."""
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")

    job = _create_job(f"relational:{req.provider}", f"Lenguaje relacional caso {case_id}", case_id=case_id)

    def worker(job_id: str):
        _update_job(job_id, step=f"Analizando expresiones relacionales ({req.provider})...", progress=30,
                    log="Evaluando regionalismos, diminutivos, emojis, sarcasmo y contexto...")
        out = run_case_relational_analysis(case_id, provider=req.provider)
        st = (out or {}).get("stats") or {}
        _update_job(
            job_id,
            status="done",
            step="Análisis relacional completado",
            progress=100,
            finished_at=_now_iso(),
            log=f"Detectadas {st.get('total', 0)} señales ({st.get('explicit', 0)} explícitas, "
                f"{st.get('ambiguous', 0)} ambiguas, {st.get('model_inference', 0)} inferencias de modelo).",
            result={"kind": "relational", **(out or {})},
        )

    _run_in_background(job["id"], worker)
    return {"job_id": job["id"], "status": "running"}


@router.patch("/api/cases/{case_id}/relational/{signal_id}")
def api_case_relational_review(case_id: str, signal_id: int, req: RelationalReviewPatchRequest):
    """Permite al analista validar, marcar como ambigua o descartar una señal relacional y actualiza SNA."""
    updated = storage.update_relational_signal_review(
        case_id=case_id,
        signal_id=signal_id,
        review_status=req.review_status,
        category=req.category,
        reviewer_note=req.reviewer_note,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Señal relacional no encontrada.")
    storage.compute_and_save_case_sna(case_id, record_run=False)
    return {"signal": updated, "relational": storage.list_relational_signals(case_id)}


@router.get("/api/cases/{case_id}/evidences")
def api_case_evidences(case_id: str, limit: int = 120):
    """Devuelve snapshots históricos del perfil, registros de evidencia y reacciones del caso."""
    data = storage.get_case_evidences(case_id, limit=max(1, min(limit, 300)))
    if data is None:
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return data


# ---------------------------------------------------------------------------
# Agente de Navegación Supervisada (Playwright Headed + Pause/Resume/Cancel + Staging)
# ---------------------------------------------------------------------------
@router.get("/api/agent/sessions")
def api_agent_sessions():
    """Estado de Playwright y sesiones locales persistentes por plataforma."""
    return {
        "playwright_ready": playwright_available(),
        "sessions": [session_status("instagram"), session_status("facebook")],
    }


@router.post("/api/agent/session/open")
def api_agent_open_session(req: BrowserSessionOpenRequest):
    """Abre Chromium visible en la página oficial de login para autenticación manual segura."""
    try:
        return open_interactive_login_window(req.platform)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/api/agent/session/{platform}")
def api_agent_clear_session(platform: str):
    """Elimina cookies y sesión local almacenada en `data/browser_profiles/<platform>`."""
    if platform not in ("instagram", "facebook"):
        raise HTTPException(status_code=400, detail="Plataforma no válida.")
    cleared = clear_platform_session(platform)
    return {"platform": platform, "cleared": cleared}


@router.post("/api/agent/session/clean-cache")
def api_agent_clean_cache(req: BrowserCacheCleanRequest = BrowserCacheCleanRequest()):
    """Limpia cachés pesados de Chromium/Edge sin eliminar cookies ni sesiones guardadas."""
    try:
        return clean_browser_cache(platform=req.platform or None, dry_run=req.dry_run)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/cases/{case_id}/agent/start")
def api_agent_start(case_id: str, req: BrowserAgentStartRequest):
    _require_legitimate(req.confirm_legitimate)
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    try:
        return start_browser_investigation(
            case_id=case_id,
            platform=req.platform,
            target_url=req.target_url,
            max_posts=req.max_posts,
            max_comments_per_post=req.max_comments_per_post,
            delay_seconds=req.delay_seconds,
            headless=False,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/cases/{case_id}/agent/runs")
def api_agent_case_runs(case_id: str):
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return {"runs": storage.list_browser_agent_runs(case_id)}


@router.get("/api/agent/runs/{run_id}")
def api_agent_run_detail(run_id: str):
    run = storage.get_browser_agent_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Ejecución de agente no encontrada.")
    return run


@router.post("/api/agent/runs/{run_id}/pause")
def api_agent_run_pause(run_id: str):
    ctrl = get_run_control(run_id)
    if not ctrl:
        raise HTTPException(status_code=400, detail="La ejecución no está activa.")
    ctrl.pause("Pausado por el usuario")
    return storage.get_browser_agent_run(run_id)


@router.post("/api/agent/runs/{run_id}/resume")
def api_agent_run_resume(run_id: str):
    ctrl = get_run_control(run_id)
    if not ctrl:
        raise HTTPException(status_code=400, detail="La ejecución no está activa.")
    ctrl.resume()
    return storage.get_browser_agent_run(run_id)


@router.post("/api/agent/runs/{run_id}/cancel")
def api_agent_run_cancel(run_id: str):
    ctrl = get_run_control(run_id)
    if not ctrl:
        raise HTTPException(status_code=400, detail="La ejecución no está activa.")
    ctrl.cancel()
    return storage.get_browser_agent_run(run_id)


@router.patch("/api/agent/runs/{run_id}/items/{item_id}")
def api_agent_patch_item(run_id: str, item_id: int, req: StagedItemPatchRequest):
    ok = storage.update_staged_item_selection(run_id, item_id, req.selected)
    if not ok:
        raise HTTPException(status_code=404, detail="Elemento no encontrado en staging.")
    return {"updated": True, "selected": req.selected}


@router.delete("/api/agent/runs/{run_id}/items/{item_id}")
def api_agent_delete_item(run_id: str, item_id: int):
    ok = storage.delete_staged_item(run_id, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Elemento no encontrado en staging.")
    return {"deleted": True}


@router.post("/api/agent/runs/{run_id}/commit")
def api_agent_commit_run(run_id: str, req: AgentCommitRequest):
    res = commit_staged_run_to_case(
        run_id=run_id,
        run_relational=req.run_relational,
        run_nlp=req.run_nlp,
        run_sna=req.run_sna,
    )
    if res is None:
        raise HTTPException(status_code=404, detail="Ejecución de agente no encontrada.")
    return res


# ---------------------------------------------------------------------------
# Interactuantes, Escaneo Secundario y Gestión Aislada de Amigos
# ---------------------------------------------------------------------------
@router.get("/api/cases/{case_id}/interactants")
def api_case_interactants(case_id: str):
    """Lista nominal de interactuantes del caso con métricas de comentarios y sentimiento."""
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    items = storage.list_case_interactants(case_id)
    return {"case_id": case_id, "count": len(items), "interactants": items}


@router.post("/api/cases/{case_id}/agent/friend-scan")
def api_case_friend_scan_start(case_id: str, req: FriendScanStartRequest):
    """Encola pasada secundaria sobre cuentas amigas candidatas (requiere `authorized=True`)."""
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    if not req.authorized:
        raise HTTPException(status_code=403, detail="Debes confirmar la autorización explícita (`authorized=true`).")
    try:
        return browser_agent.start_friend_scan_queue(
            case_id=case_id,
            authorized=req.authorized,
            parent_run_id=req.parent_run_id,
            platform=req.platform,
            delay_seconds=req.delay_seconds,
            headless=req.headless,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/cases/{case_id}/agent/friend-scan/{run_id}")
def api_case_friend_scan_status(case_id: str, run_id: str):
    """Devuelve estado, progreso y cola de `friend_scan_runs` del caso."""
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    scans = storage.list_friend_scan_runs(case_id)
    parent = storage.get_browser_agent_run(run_id) if run_id and run_id != "none" else None
    done_n = sum(1 for s in scans if s.get("status") == "done")
    paused_n = sum(1 for s in scans if s.get("status") == "paused_checkpoint")
    return {
        "case_id": case_id,
        "run_id": run_id,
        "total": len(scans),
        "completed": done_n,
        "paused_checkpoint": paused_n,
        "resume_cursor": (parent or {}).get("resume_cursor") or "",
        "scans": scans,
    }


@router.get("/api/cases/{case_id}/friend-evidence")
def api_case_friend_evidence(case_id: str):
    """Devuelve pares visuales coincidentes (`dHash`) e historial consolidado por cuenta amiga."""
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    return browser_agent.get_case_friend_evidence(case_id)


@router.post("/api/cases/{case_id}/friends")
def api_case_add_friend(case_id: str, req: FriendAddRequest):
    """Registra manualmente un amigo por URL pública (aislado del sujeto; nunca evade login)."""
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    url_low = req.url.lower()
    if any(m in url_low for m in ("/login", "/checkpoint", "/challenge", "authwall")):
        status = "pending_manual"
    else:
        status = "active"
    friend = storage.add_friend_by_url(
        case_id=case_id,
        url=req.url,
        platform=req.platform,
        label=req.label,
        status=status,
    )
    return {"case_id": case_id, "friend": friend}


@router.get("/api/cases/{case_id}/friends")
def api_case_list_friends(case_id: str):
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    friends = storage.list_case_friends(case_id)
    return {"case_id": case_id, "count": len(friends), "friends": friends}


@router.get("/api/cases/{case_id}/friends/{friend_id}")
def api_case_friend_detail(case_id: str, friend_id: int):
    detail = storage.get_friend_isolated_detail(case_id, friend_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Amigo no encontrado en este caso.")
    return {"case_id": case_id, **detail}


@router.post("/api/cases/{case_id}/friends/{friend_id}/investigate")
def api_case_friend_investigate(case_id: str, friend_id: int, req: FriendInvestigateRequest = FriendInvestigateRequest()):
    """Ejecuta pipeline aislado sobre el contenido propio del amigo; comparación dHash es opt-in."""
    detail = storage.get_friend_isolated_detail(case_id, friend_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Amigo no encontrado.")

    from modules.geo_inference import extract_media_gps, infer_case_locations
    from modules.vision_analysis import build_geo_vlm_client, compare_media_between_profiles, extract_geo_hints

    f_media = detail.get("media_items") or []
    f_posts = detail.get("posts") or []
    post_cap = {p.get("id"): (p.get("content") or "") for p in f_posts}
    geo_created = 0
    for m in f_media:
        gps = extract_media_gps(m)
        lp = Path(m["local_path"]) if m.get("local_path") else None
        vlm_cb = build_geo_vlm_client(lp, m.get("source_url") or "") if req.use_vlm else None
        ex = extract_geo_hints(
            image_path=lp if (lp and lp.is_file()) else None,
            vlm_client=vlm_cb,
            ocr_text=m.get("ocr_text") or "",
            alt_text=m.get("alt_text") or "",
            caption=post_cap.get(m.get("post_id"), ""),
            exif_gps=gps,
        )
        hints = ex.get("hints") or []
        if hints:
            inf = infer_case_locations(geo_hints=hints, media_items=[m], posts=[], nlp_data={})
            cands = inf.get("candidates") or []
            if cands:
                top = cands[0]
                storage.save_location_history_entry(
                    case_id=case_id,
                    media_id=m["id"],
                    owner_type="friend",
                    owner_id=friend_id,
                    source=top.get("source") or "local_context",
                    country=top.get("country") or "",
                    region=top.get("region") or "",
                    city=top.get("city") or "",
                    lat=top.get("lat"),
                    lon=top.get("lon"),
                    confidence=float(top.get("confidence") or 0.5),
                    evidence_quote="; ".join(top.get("evidence") or [])[:380],
                    published_at=m.get("published_at") or m.get("created_at") or "",
                )
                geo_created += 1

    visual_matches = []
    visual_score = 0.0
    if req.compare_with_subject:
        subj_content = storage.get_case_content(case_id, include_friends=False) or {}
        subj_media = subj_content.get("media_items") or []
        visual_matches = compare_media_between_profiles(subj_media, f_media)
        if visual_matches:
            visual_score = min(100.0, len(visual_matches) * 28.0)
        storage.record_case_run(
            case_id,
            kind="audit:friend_visual_compare",
            target=detail["friend"]["handle"],
            status="done",
            summary={"friend_id": friend_id, "matches": len(visual_matches), "visual_proximity_score": visual_score},
        )

    return {
        "case_id": case_id,
        "friend_id": friend_id,
        "locations_inferred": geo_created,
        "compared_with_subject": bool(req.compare_with_subject),
        "visual_matches": visual_matches,
        "visual_proximity_score": visual_score,
    }


@router.post("/api/cases/{case_id}/friends/{friend_id}/crawl")
def api_case_friend_crawl(case_id: str, friend_id: int):
    """Captura perfil/publicaciones/fotos de un amigo añadido por link hacia su almacén aislado."""
    if not storage.case_exists(case_id):
        raise HTTPException(status_code=404, detail="Caso no encontrado.")
    try:
        return browser_agent.crawl_single_friend_profile(case_id=case_id, friend_id=friend_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/cases/{case_id}/friends/{friend_id}/link-sna")
def api_case_friend_link_sna(case_id: str, friend_id: int, req: FriendLinkSnaRequest):
    """Confirmación explícita del analista para enlazar un amigo al grafo SNA del sujeto."""
    if not req.confirm:
        raise HTTPException(status_code=403, detail="Se requiere confirmación explícita (`confirm=true`) para enlazar al SNA del sujeto.")
    res = storage.link_friend_to_subject_sna(
        case_id=case_id,
        friend_id=friend_id,
        visual_score=req.visual_proximity_score,
        matches_count=req.matches_count,
    )
    if not res:
        raise HTTPException(status_code=404, detail="Caso o amigo no encontrado.")
    return res


@router.delete("/api/cases/{case_id}/friends/{friend_id}")
def api_case_delete_friend(case_id: str, friend_id: int):
    ok = storage.delete_friend_cascade(case_id, friend_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Amigo no encontrado.")
    return {"case_id": case_id, "friend_id": friend_id, "deleted": True}
