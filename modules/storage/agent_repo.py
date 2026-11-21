# -*- coding: utf-8 -*-
"""Repositorio del agente de navegación, staging, interactuantes y escaneo secundario."""
import json
import os
import uuid
from typing import Optional

from .db import _connect, _loads, _now_iso
from .cases_repo import case_exists
from .ingest_repo import _upsert_interactant_conn

# ---------------------------------------------------------------------------
# Agente de Navegación Supervisada :: Runs y Bandeja de Revisión (Staging)
# ---------------------------------------------------------------------------
# Para qué sirve: registra una nueva ejecución del agente de navegación supervisada en SQLite.
# Cómo funciona: inserta la fila inicial en `browser_agent_runs` (estado `running`) y retorna el registro recién creado.
def create_browser_agent_run(
    case_id: str,
    platform: str,
    target_url: str,
    limits: Optional[dict] = None,
    run_id: Optional[str] = None,
) -> Optional[dict]:
    """Crea un registro de ejecución del agente de navegación en `browser_agent_runs`."""
    if not case_exists(case_id):
        return None
    rid = run_id or uuid.uuid4().hex[:12]
    now = _now_iso()
    logs = [f"[{now}] Sesión de agente iniciada ({platform}): {target_url}"]
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO browser_agent_runs (
                id, case_id, platform, target_url, status, step, progress,
                limits_json, stats_json, logs_json, error, committed,
                started_at, updated_at, finished_at
            ) VALUES (?, ?, ?, ?, 'running', 'Iniciando navegador...', 5, ?, '{}', ?, '', 0, ?, ?, '')
            """,
            (
                rid,
                case_id,
                platform.strip().lower(),
                target_url.strip(),
                json.dumps(limits or {}, ensure_ascii=False),
                json.dumps(logs, ensure_ascii=False),
                now,
                now,
            ),
        )
    return get_browser_agent_run(rid)


# Para qué sirve: actualiza progreso, logs (máx. 120 entradas), estado y cursor de reanudación de una corrida.
# Cómo funciona: lee la fila actual, conserva campos no enviados y ejecuta `UPDATE browser_agent_runs`.
def update_browser_agent_run(
    run_id: str,
    status: Optional[str] = None,
    step: Optional[str] = None,
    progress: Optional[int] = None,
    log: Optional[str] = None,
    stats: Optional[dict] = None,
    error: Optional[str] = None,
    committed: Optional[int] = None,
    resume_cursor: Optional[str] = None,
    finished: bool = False,
) -> Optional[dict]:
    """Actualiza estado, progreso, logs y cursor de reanudación de una corrida del agente."""
    now = _now_iso()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM browser_agent_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        logs = _loads(row["logs_json"], [])
        if log:
            logs.append(f"[{now}] {log}")
            logs = logs[-120:]
        new_status = status if status is not None else row["status"]
        new_step = step if step is not None else row["step"]
        new_prog = max(0, min(100, int(progress))) if progress is not None else row["progress"]
        new_stats = json.dumps(stats, ensure_ascii=False) if stats is not None else row["stats_json"]
        new_err = error if error is not None else row["error"]
        new_comm = int(committed) if committed is not None else row["committed"]
        new_cursor = resume_cursor if resume_cursor is not None else (row["resume_cursor"] if "resume_cursor" in row.keys() else "")
        fin_at = now if finished else row["finished_at"]
        conn.execute(
            """
            UPDATE browser_agent_runs
            SET status = ?, step = ?, progress = ?, stats_json = ?, logs_json = ?,
                error = ?, committed = ?, resume_cursor = ?, updated_at = ?, finished_at = ?
            WHERE id = ?
            """,
            (new_status, new_step, new_prog, new_stats, json.dumps(logs, ensure_ascii=False), new_err, new_comm, new_cursor, now, fin_at, run_id),
        )
    return get_browser_agent_run(run_id)


# Para qué sirve: guarda un elemento recolectado en la bandeja temporal de revisión (`staging`) antes de integrarlo al caso.
# Cómo funciona: inserta en `browser_staged_items` con `selected=1`, normaliza handles y serializa `payload_json`.
def add_browser_staged_item(
    run_id: str,
    case_id: str,
    item_kind: str,
    platform: str,
    author_handle: str = "",
    target_handle: str = "",
    content: str = "",
    published_at: str = "",
    source_url: str = "",
    payload: Optional[dict] = None,
) -> Optional[dict]:
    """Inserta un hallazgo en la bandeja temporal de revisión (`browser_staged_items`)."""
    now = _now_iso()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO browser_staged_items (
                run_id, case_id, item_kind, platform, author_handle, target_handle,
                content, published_at, source_url, payload_json, selected, captured_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
            """,
            (
                run_id,
                case_id,
                item_kind,
                platform.strip().lower(),
                (author_handle or "").strip().lstrip("@").lower(),
                (target_handle or "").strip().lstrip("@").lower(),
                (content or "")[:2000],
                published_at or "",
                source_url or "",
                json.dumps(payload or {}, ensure_ascii=False),
                now,
            ),
        )
        row = conn.execute("SELECT * FROM browser_staged_items WHERE id = ?", (cur.lastrowid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["payload"] = _loads(d.pop("payload_json", "{}"), {})
    return d


# Para qué sirve: recupera una corrida del agente junto con todos sus ítems pendientes o aprobados en staging.
# Cómo funciona: consulta `browser_agent_runs` + `browser_staged_items` y parsea los JSON de límites, stats, logs y payload.
def get_browser_agent_run(run_id: str) -> Optional[dict]:
    """Devuelve el detalle de una ejecución del agente junto con sus ítems en staging."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM browser_agent_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            return None
        item_rows = conn.execute(
            "SELECT * FROM browser_staged_items WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        ).fetchall()
    d = dict(row)
    d["limits"] = _loads(d.pop("limits_json", "{}"), {})
    d["stats"] = _loads(d.pop("stats_json", "{}"), {})
    d["logs"] = _loads(d.pop("logs_json", "[]"), [])
    items = []
    for ir in item_rows:
        it = dict(ir)
        it["payload"] = _loads(it.pop("payload_json", "{}"), {})
        items.append(it)
    d["items"] = items
    return d


# Para qué sirve: enumera las últimas 20 ejecuciones del agente navegador pertenecientes a un caso.
# Cómo funciona: consulta `browser_agent_runs` ordenadas por `started_at DESC` y deserializa sus campos JSON.
def list_browser_agent_runs(case_id: str) -> list:
    """Lista las ejecuciones del agente asociadas a `case_id`."""
    if not case_exists(case_id):
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM browser_agent_runs WHERE case_id = ? ORDER BY started_at DESC LIMIT 20",
            (case_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["limits"] = _loads(d.pop("limits_json", "{}"), {})
        d["stats"] = _loads(d.pop("stats_json", "{}"), {})
        d["logs"] = _loads(d.pop("logs_json", "[]"), [])
        out.append(d)
    return out


# Para qué sirve: permite al analista marcar o desmarcar un ítem de staging antes de hacer el commit al caso.
# Cómo funciona: actualiza la columna `selected` (`1` o `0`) en `browser_staged_items`.
def update_staged_item_selection(run_id: str, item_id: int, selected: bool) -> bool:
    """Marca o desmarca un elemento de staging para incluirlo en el commit al caso."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE browser_staged_items SET selected = ? WHERE id = ? AND run_id = ?",
            (1 if selected else 0, item_id, run_id),
        )
        return cur.rowcount > 0


# Para qué sirve: descarta definitivamente un elemento de la bandeja temporal antes de integrarlo al caso.
# Cómo funciona: ejecuta `DELETE` sobre `browser_staged_items` filtrando por `id` y `run_id`.
def delete_staged_item(run_id: str, item_id: int) -> bool:
    """Elimina un ítem específico de `browser_staged_items` antes del commit."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM browser_staged_items WHERE id = ? AND run_id = ?",
            (item_id, run_id),
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Módulos A, B y C :: Interactuantes, Friend Scan y Pistas de Ubicación Visual
# ---------------------------------------------------------------------------
# Para qué sirve: crea o actualiza un tercero interactuante deduplicado dentro del caso.
# Cómo funciona: abre conexión si el caso existe y delega en `_upsert_interactant_conn`.
def upsert_interactant(
    case_id: str,
    platform: str,
    handle: str = "",
    display_name: str = "",
    profile_url: str = "",
    seen_at: str = "",
) -> Optional[int]:
    """Crea o actualiza un interactuante nominal deduplicado y devuelve su `id`."""
    if not case_exists(case_id):
        return None
    with _connect() as conn:
        return _upsert_interactant_conn(conn, case_id, platform, handle, display_name, profile_url, seen_at)


# Para qué sirve: lista todos los interactuantes del caso enriquecidos con conteo de comentarios, sentimiento, reciprocidad y cercanía visual.
# Cómo funciona: cruza `interactants`, `comments` e `interactions`, calcula `is_friend_candidate` y ordena por relevancia.
def list_case_interactants(case_id: str) -> list:
    """Devuelve interactuantes nominales del caso con métricas de comentarios y sentimiento."""
    if not case_exists(case_id):
        return []
    min_inter = int(os.getenv("FRIEND_CANDIDATE_MIN_INTERACTIONS", "2") or 2)
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM interactants WHERE case_id = ? ORDER BY last_seen DESC, id ASC",
            (case_id,),
        ).fetchall()
        com_rows = conn.execute(
            "SELECT id, post_id, author_handle, target_handle, sentiment, published_at, interactant_id FROM comments WHERE case_id = ?",
            (case_id,),
        ).fetchall()
        inter_rows = conn.execute(
            "SELECT source_handle, target_handle, weight, evidence_count, visual_proximity_score FROM interactions WHERE case_id = ?",
            (case_id,),
        ).fetchall()

    # Detectar reciprocidad por pares
    pairs = {(str(r["source_handle"] or "").lower(), str(r["target_handle"] or "").lower()) for r in inter_rows}
    vis_by_handle = {}
    inter_count_by_handle = {}
    for r in inter_rows:
        s = str(r["source_handle"] or "").lower()
        t = str(r["target_handle"] or "").lower()
        vp = float(r["visual_proximity_score"] or 0)
        ev = int(r["evidence_count"] or 1)
        if s:
            vis_by_handle[s] = max(vis_by_handle.get(s, 0.0), vp)
            inter_count_by_handle[s] = inter_count_by_handle.get(s, 0) + ev
        if t:
            vis_by_handle[t] = max(vis_by_handle.get(t, 0.0), vp)
            inter_count_by_handle[t] = inter_count_by_handle.get(t, 0) + ev

    out = []
    for r in rows:
        d = dict(r)
        iid = d["id"]
        h = (d.get("handle") or "").lower()
        matched_coms = [c for c in com_rows if c["interactant_id"] == iid or (h and str(c["author_handle"] or "").lower() == h)]
        sent_counts = {"POSITIVO": 0, "NEGATIVO": 0, "NEUTRAL": 0}
        post_ids = []
        for c in matched_coms:
            sent = (c["sentiment"] or "NEUTRAL").upper()
            sent_counts[sent] = sent_counts.get(sent, 0) + 1
            if c["post_id"] and c["post_id"] not in post_ids:
                post_ids.append(c["post_id"])
        dominant = "NEUTRAL"
        if sent_counts["POSITIVO"] > sent_counts["NEGATIVO"] and sent_counts["POSITIVO"] > 0:
            dominant = "POSITIVO"
        elif sent_counts["NEGATIVO"] > sent_counts["POSITIVO"] and sent_counts["NEGATIVO"] > 0:
            dominant = "NEGATIVO"
        reciprocal = any((a, b) in pairs and (b, a) in pairs for a, b in pairs if a == h or b == h)
        total_ev = max(len(matched_coms), inter_count_by_handle.get(h, 0))
        d["comments_count"] = len(matched_coms)
        d["interactions_count"] = total_ev
        d["post_ids"] = post_ids
        d["sentiment_counts"] = sent_counts
        d["dominant_sentiment"] = dominant
        d["reciprocal"] = reciprocal
        d["is_friend_candidate"] = bool(reciprocal or total_ev >= min_inter)
        d["visual_proximity_score"] = round(vis_by_handle.get(h, 0.0), 1)
        out.append(d)
    out.sort(key=lambda x: (-int(x["is_friend_candidate"]), -x["comments_count"], -x["interactions_count"], x["handle"]))
    return out


# Para qué sirve: selecciona qué interactuantes del caso califican para la pasada secundaria `friend-scan`.
# Cómo funciona: filtra `list_case_interactants` por reciprocidad o `interactions_count >= thr` hasta `MAX_FRIEND_PROFILES`.
def get_friend_candidate_handles(case_id: str, min_interactions: Optional[int] = None) -> list:
    """Devuelve interactuantes elegibles para `friend-scan` (≥ min_interactions o reciprocidad)."""
    thr = int(min_interactions if min_interactions is not None else (os.getenv("FRIEND_CANDIDATE_MIN_INTERACTIONS", "2") or 2))
    max_profiles = int(os.getenv("MAX_FRIEND_PROFILES", "10") or 10)
    all_it = list_case_interactants(case_id)
    cands = [it for it in all_it if it.get("reciprocal") or int(it.get("interactions_count") or 0) >= thr]
    return cands[:max_profiles]


# Para qué sirve: registra un trabajo en cola para escanear una cuenta amiga candidata.
# Cómo funciona: inserta la fila en `friend_scan_runs` con estado `'queued'` y `resume_cursor` del padre.
def create_friend_scan_run(
    case_id: str,
    platform: str,
    target_handle: str,
    target_url: str = "",
    interactant_id: Optional[int] = None,
    parent_run_id: str = "",
    resume_cursor: str = "",
) -> Optional[dict]:
    """Encola un escaneo secundario en `friend_scan_runs` para una cuenta candidata."""
    if not case_exists(case_id):
        return None
    now = _now_iso()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO friend_scan_runs (
                case_id, parent_run_id, interactant_id, target_handle, target_url,
                platform, status, started_at, resume_cursor
            ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (case_id, parent_run_id, interactant_id, target_handle.strip().lstrip("@").lower(), target_url, platform.strip().lower(), now, resume_cursor),
        )
        row = conn.execute("SELECT * FROM friend_scan_runs WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row) if row else None


# Para qué sirve: actualiza el avance, coincidencias visuales o error de un escaneo de cuenta amiga.
# Cómo funciona: lee el registro por `scan_id`, mezcla los campos opcionales y ejecuta `UPDATE friend_scan_runs`.
def update_friend_scan_run(
    scan_id: int,
    status: Optional[str] = None,
    items_scanned: Optional[int] = None,
    visual_matches: Optional[int] = None,
    resume_cursor: Optional[str] = None,
    error: Optional[str] = None,
    finished: bool = False,
) -> Optional[dict]:
    """Actualiza estado, conteo de coincidencias visuales y cursor de un `friend_scan_run`."""
    now = _now_iso()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM friend_scan_runs WHERE id = ?", (scan_id,)).fetchone()
        if not row:
            return None
        new_status = status if status is not None else row["status"]
        new_items = int(items_scanned) if items_scanned is not None else int(row["items_scanned"] or 0)
        new_matches = int(visual_matches) if visual_matches is not None else int(row["visual_matches"] or 0)
        new_cursor = resume_cursor if resume_cursor is not None else (row["resume_cursor"] or "")
        new_err = error if error is not None else (row["error"] or "")
        fin_at = now if finished else (row["finished_at"] or "")
        conn.execute(
            """
            UPDATE friend_scan_runs
            SET status = ?, items_scanned = ?, visual_matches = ?, resume_cursor = ?, error = ?, finished_at = ?
            WHERE id = ?
            """,
            (new_status, new_items, new_matches, new_cursor, new_err, fin_at, scan_id),
        )
        out = conn.execute("SELECT * FROM friend_scan_runs WHERE id = ?", (scan_id,)).fetchone()
    return dict(out) if out else None


# Para qué sirve: lista todos los escaneos secundarios registrados para un caso en orden FIFO.
# Cómo funciona: consulta `friend_scan_runs` filtrando por `case_id` ordenado por `id ASC`.
def list_friend_scan_runs(case_id: str) -> list:
    """Lista la cola histórica y actual de escaneos secundarios del caso."""
    if not case_exists(case_id):
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM friend_scan_runs WHERE case_id = ? ORDER BY id ASC",
            (case_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# Para qué sirve: recupera un registro individual de `friend_scan_runs` por su ID numérico.
# Cómo funciona: ejecuta `SELECT * FROM friend_scan_runs WHERE id = ?`.
def get_friend_scan_run(scan_id: int) -> Optional[dict]:
    """Obtiene el registro de un escaneo secundario por su `id`."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM friend_scan_runs WHERE id = ?", (scan_id,)).fetchone()
    return dict(row) if row else None


# Para qué sirve: registra la máxima similitud visual `dHash` detectada sobre las aristas de un tercero.
# Cómo funciona: actualiza `visual_proximity_score` usando `MAX(...)` en todas las filas de `interactions` del handle.
def update_interaction_visual_score(case_id: str, handle: str, score: float) -> None:
    """Actualiza el puntaje máximo de proximidad visual dHash en las interacciones de `handle`."""
    h = (handle or "").strip().lstrip("@").lower()
    if not case_id or not h:
        return
    with _connect() as conn:
        conn.execute(
            """
            UPDATE interactions
            SET visual_proximity_score = MAX(COALESCE(visual_proximity_score, 0), ?)
            WHERE case_id = ? AND (LOWER(source_handle) = ? OR LOWER(target_handle) = ?)
            """,
            (round(float(score), 1), case_id, h, h),
        )


