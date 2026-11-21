# -*- coding: utf-8 -*-
"""
Orquestador de Investigación Autorizada de una Cuenta Seleccionada.

Coordina de forma modular (sin duplicar lógica existente):
  1. Verificación de capacidades de la plataforma y resolución de token OAuth2 (si aplica).
  2. Extracción autorizada (perfil, publicaciones, media, comentarios/respuestas, reacciones).
  3. Persistencia con snapshots inmutables y registros de evidencia (`storage.py`).
  4. Análisis de lenguaje relacional contextual (`relational_language.py`).
  5. Actualización de NLP (`text_analysis.py`) y Grafo SNA (`sna_graph.py`).
"""
from typing import Callable, Optional

from modules import storage
from modules.connectors import ALL_PLATFORMS, fetch_from_connector
from modules.platform_auth import get_valid_access_token
from modules.relational_language import analyze_case_relational_language
from modules.text_analysis import analyze_case_nlp


def run_case_relational_analysis(case_id: str, provider: str = "local") -> Optional[dict]:
    """
    Ejecuta el motor de lenguaje relacional sobre todos los comentarios y publicaciones
    de un caso, aprovechando los pares recíprocos del SNA como co-evidencia contextual,
    guarda resultados preservando revisiones humanas y actualiza el SNA.
    """
    content = storage.get_case_content(case_id, limit_posts=200, limit_comments=300, limit_media=10)
    if content is None:
        return None

    # Determinar pares recíprocos existentes en interactions para co-evidencia
    directed = set()
    for ix in content.get("interactions") or []:
        s = (ix.get("source_handle") or "").lower()
        t = (ix.get("target_handle") or "").lower()
        if s and t and s != t:
            directed.add((s, t))
    reciprocal_pairs = {
        (min(s, t), max(s, t))
        for (s, t) in directed
        if (t, s) in directed
    }

    signals = analyze_case_relational_language(
        comments=content.get("comments") or [],
        posts=content.get("posts") or [],
        reciprocal_pairs=reciprocal_pairs,
        provider=provider,
    )
    save_stats = storage.save_relational_signals(case_id, signals, replace_pending=True)
    # Actualizar SNA para que refleje los `relational_cues` actualizados
    storage.compute_and_save_case_sna(case_id, record_run=False)
    listed = storage.list_relational_signals(case_id)
    return {
        "case_id": case_id,
        "provider": provider,
        "save_stats": save_stats,
        "stats": (listed or {}).get("stats") or {},
        "signals": (listed or {}).get("signals") or [],
    }


def investigate_social_account(
    case_id: str,
    platform: str,
    handle: str,
    limit: int = 25,
    include_replies: bool = True,
    include_reactions: bool = True,
    connection_id: Optional[str] = None,
    run_relational: bool = True,
    relational_provider: str = "local",
    run_nlp: bool = True,
    nlp_provider: str = "local",
    run_sna: bool = True,
    job_id: str = "",
    progress_cb: Optional[Callable[[str, int, str], None]] = None,
) -> dict:
    """
    Ejecuta la investigación integral autorizada de una cuenta seleccionada dentro de un caso.
    """
    def _emit(step: str, pct: int, log: str):
        if progress_cb:
            progress_cb(step, pct, log)

    plat = (platform or "").strip().lower()
    if plat not in ALL_PLATFORMS:
        raise ValueError(f"Plataforma no soportada: {platform}")
    if not storage.case_exists(case_id):
        raise ValueError(f"Caso no encontrado: {case_id}")

    caps = ALL_PLATFORMS[plat]["capabilities"]
    _emit(f"Paso 1/5 :: Validando acceso autorizado para {caps['name']}...", 10, f"Plataforma: {caps['name']} (modo {caps['auth_mode']}).")

    access_token = None
    if plat in ("instagram_pro", "facebook_page", "reddit"):
        access_token, conn_meta = get_valid_access_token(plat, conn_id=connection_id)
        if conn_meta:
            _emit(f"Usando conexión OAuth2 autorizada ({conn_meta.get('account_label')})...", 18, f"Token OAuth2 activo: {conn_meta.get('account_label')}.")

    _emit(f"Paso 2/5 :: Extrayendo perfil, publicaciones, interacciones y reacciones ({plat})...", 30, f"Consultando '{handle}' en {plat}...")
    bundle = fetch_from_connector(
        platform=plat,
        handle=handle,
        limit=limit,
        include_replies=include_replies,
        include_reactions=include_reactions,
        fetch_post_replies=include_replies,
        access_token=access_token,
    )

    _emit("Paso 3/5 :: Guardando snapshot, evidencias trazables y contenido en SQLite...", 58,
          f"Extraído: {len(bundle['posts'])} posts, {len(bundle['comments'])} comentarios, "
          f"{len(bundle['reactions'])} reacciones, {len(bundle['media_items'])} media.")
    ingest_summary = storage.ingest_connector_bundle(case_id=case_id, bundle=bundle, job_id=job_id)

    relational_summary = None
    if run_relational:
        _emit("Paso 4/5 :: Analizando lenguaje relacional contextual (familia, amistad, pareja)...", 74,
              f"Evaluando expresiones relacionales y ambigüedad ({relational_provider})...")
        relational_summary = run_case_relational_analysis(case_id, provider=relational_provider)

    nlp_summary = None
    if run_nlp:
        _emit("Paso 5/5 :: Actualizando análisis NLP y Grafo SNA...", 88,
              f"Ejecutando NLP ({nlp_provider}) y recalculando Círculo Cercano...")
        corpus = storage.get_case_corpus_for_nlp(case_id)
        if corpus:
            nlp_res = analyze_case_nlp(corpus, provider=nlp_provider)
            storage.save_case_nlp_analysis(case_id, nlp_res)
            nlp_summary = nlp_res.get("counts")

    sna_stats = None
    if run_sna:
        sna_res = storage.compute_and_save_case_sna(case_id, record_run=False)
        sna_stats = (sna_res or {}).get("stats")

    return {
        "case_id": case_id,
        "platform": plat,
        "handle": bundle.get("handle") or handle,
        "capabilities": caps,
        "access_method": bundle.get("access_method") or "public_api",
        "ingest": ingest_summary,
        "relational": (relational_summary or {}).get("stats"),
        "nlp": nlp_summary,
        "sna": sna_stats,
        "warnings": bundle.get("warnings") or [],
    }
