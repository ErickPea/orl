# -*- coding: utf-8 -*-
"""Repositorio de visión multimodal, NLP, grafo SNA, conexiones OAuth2 y lenguaje relacional."""
import json
import uuid
from typing import Optional

from .db import _connect, _loads, _now_iso
from .cases_repo import _get_primary_entity_id, case_exists, record_case_run

# ---------------------------------------------------------------------------
# Fase 3 :: Galería local y persistencia de Análisis Visual (VLM + dHash + OCR)
# ---------------------------------------------------------------------------
def add_case_gallery_image(
    case_id: str,
    filename: str,
    local_path: str,
    public_url: str,
    folder: str,
    sha256: str = "",
    md5: str = "",
    file_size: int = 0,
    alt_text: str = "",
    gps_lat: Optional[float] = None,
    gps_lon: Optional[float] = None,
    captured_date: str = "",
    device: str = "",
    metadata: Optional[dict] = None,
) -> Optional[dict]:
    """Registra una foto subida directamente a la galería del caso (evitando duplicados por SHA-256)."""
    if not case_exists(case_id):
        return None
    now = _now_iso()
    with _connect() as conn:
        entity_id = _get_primary_entity_id(conn, case_id)
        if sha256:
            ex = conn.execute(
                "SELECT * FROM media_items WHERE case_id = ? AND sha256 = ?",
                (case_id, sha256),
            ).fetchone()
            if ex:
                d = dict(ex)
                d["metadata"] = _loads(d.pop("metadata_json", "{}"), {})
                d["tags"] = _loads(d.pop("tags_json", "[]"), [])
                d["vision"] = _loads(d.pop("vision_json", "{}"), {})
                return d
        cur = conn.execute(
            """
            INSERT INTO media_items (
                case_id, entity_id, filename, media_type, sha256, md5, file_size,
                gps_lat, gps_lon, captured_date, device, folder, clean_copy_url,
                source_url, alt_text, local_path, metadata_json, tags_json, vision_json, created_at
            ) VALUES (?, ?, ?, 'image', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '{}', ?)
            """,
            (
                case_id,
                entity_id,
                filename,
                sha256,
                md5,
                int(file_size or 0),
                gps_lat,
                gps_lon,
                captured_date or "",
                device or "",
                folder or "",
                public_url or "",
                public_url or "",
                (alt_text or "")[:400],
                local_path or "",
                json.dumps(metadata or {}, ensure_ascii=False),
                now,
            ),
        )
        conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
        row = conn.execute("SELECT * FROM media_items WHERE id = ?", (cur.lastrowid,)).fetchone()
    d = dict(row)
    d["metadata"] = _loads(d.pop("metadata_json", "{}"), {})
    d["tags"] = _loads(d.pop("tags_json", "[]"), [])
    d["vision"] = _loads(d.pop("vision_json", "{}"), {})
    return d


def update_media_vision(case_id: str, media_id: int, vision_result: dict) -> bool:
    """Guarda el resultado de análisis visual (dHash, OCR, etiquetas y JSON completo) para una foto."""
    if not case_exists(case_id):
        return False
    tags = [
        t["label"] if isinstance(t, dict) else str(t)
        for t in (vision_result.get("themes") or [])
        if (t.get("label") if isinstance(t, dict) else str(t))
    ]
    now = _now_iso()
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE media_items
            SET dhash = ?, ocr_text = ?, tags_json = ?, vision_json = ?
            WHERE id = ? AND case_id = ?
            """,
            (
                vision_result.get("dhash") or "",
                (vision_result.get("ocr_text") or "")[:600],
                json.dumps(tags, ensure_ascii=False),
                json.dumps(vision_result, ensure_ascii=False),
                media_id,
                case_id,
            ),
        )
        if cur.rowcount > 0:
            conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
            return True
    return False


def get_case_media_for_vision(case_id: str, only_pending: bool = False) -> list:
    """Devuelve las imágenes del caso junto con el texto de contexto de posts asociados."""
    if not case_exists(case_id):
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM media_items WHERE case_id = ? AND media_type = 'image' ORDER BY id ASC",
            (case_id,),
        ).fetchall()
        posts_rows = conn.execute(
            "SELECT external_id, content, metadata_json FROM posts WHERE case_id = ?",
            (case_id,),
        ).fetchall()

    # Mapa auxiliar de URL/external_id -> caption del post
    caption_by_url = {}
    for pr in posts_rows:
        meta = _loads(pr["metadata_json"], {})
        for mu in meta.get("media_urls") or []:
            caption_by_url[mu] = pr["content"] or ""

    out = []
    for r in rows:
        d = dict(r)
        d["metadata"] = _loads(d.pop("metadata_json", "{}"), {})
        d["tags"] = _loads(d.pop("tags_json", "[]"), [])
        d["vision"] = _loads(d.pop("vision_json", "{}"), {})
        if only_pending and d["vision"].get("analyzed_at"):
            continue
        d["caption"] = caption_by_url.get(d.get("source_url") or "", "")
        out.append(d)
    return out


def get_case_vision_summary(case_id: str) -> Optional[dict]:
    """Devuelve galería completa + agregación de intereses visuales y duplicados dHash del caso."""
    from modules.vision_analysis import summarize_case_vision

    if not case_exists(case_id):
        return None
    items = get_case_media_for_vision(case_id, only_pending=False)
    summary = summarize_case_vision(items)
    return {
        "case_id": case_id,
        **summary,
        "media_items": items,
    }


# ---------------------------------------------------------------------------
# Fase 4 :: Corpus y Persistencia de Análisis NLP (Temas, NER, Sentimiento)
# ---------------------------------------------------------------------------
def get_case_corpus_for_nlp(case_id: str) -> Optional[dict]:
    """Recopila biografías, posts, comentarios y temas visuales (Fase 3) para el motor NLP."""
    if not case_exists(case_id):
        return None
    with _connect() as conn:
        ident_rows = conn.execute(
            "SELECT kind, value FROM identifiers WHERE case_id = ?",
            (case_id,),
        ).fetchall()
        prof_rows = conn.execute(
            """
            SELECT site, username, bio, location, manual_status, resolution_level
            FROM profiles WHERE case_id = ? AND manual_status != 'rejected'
            """,
            (case_id,),
        ).fetchall()
        post_rows = conn.execute(
            "SELECT id, platform, author, content, published_at, url FROM posts WHERE case_id = ? AND COALESCE(owner_type, 'subject') != 'friend' ORDER BY published_at DESC",
            (case_id,),
        ).fetchall()
        comment_rows = conn.execute(
            "SELECT id, author_handle, target_handle, content, published_at, sentiment FROM comments WHERE case_id = ? AND COALESCE(owner_type, 'subject') != 'friend' ORDER BY published_at DESC",
            (case_id,),
        ).fetchall()

    subject_handles = set()
    for r in ident_rows:
        if r["kind"] == "username" and r["value"]:
            subject_handles.add(r["value"].strip().lstrip("@").lower())
    bios = []
    for p in prof_rows:
        if p["username"]:
            subject_handles.add(p["username"].strip().lstrip("@").lower())
        if p["bio"] or p["location"]:
            bios.append({"site": p["site"], "text": p["bio"] or "", "location": p["location"] or ""})

    vision = get_case_vision_summary(case_id) or {}
    return {
        "subject_handles": sorted(subject_handles),
        "bios": bios,
        "posts": [dict(r) for r in post_rows],
        "comments": [dict(r) for r in comment_rows],
        "vision_themes": vision.get("theme_summary") or [],
    }


def save_case_nlp_analysis(case_id: str, nlp_result: dict) -> bool:
    """Guarda el análisis NLP en `cases.nlp_json` y actualiza `comments.sentiment`."""
    if not case_exists(case_id):
        return False
    payload = {k: v for k, v in nlp_result.items() if k != "comment_sentiments"}
    comment_sents = nlp_result.get("comment_sentiments") or {}
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            "UPDATE cases SET nlp_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), now, case_id),
        )
        for cid, label in comment_sents.items():
            conn.execute(
                "UPDATE comments SET sentiment = ? WHERE id = ? AND case_id = ?",
                (str(label), int(cid), case_id),
            )
    return True


def get_case_nlp_analysis(case_id: str) -> Optional[dict]:
    """Devuelve el último análisis NLP persistido para el caso."""
    with _connect() as conn:
        row = conn.execute("SELECT nlp_json FROM cases WHERE id = ?", (case_id,)).fetchone()
    if not row:
        return None
    data = _loads(row["nlp_json"], {})
    if not data:
        return {"case_id": case_id, "analyzed": False}
    return {"case_id": case_id, "analyzed": True, **data}


# ---------------------------------------------------------------------------
# Fase 5 :: Grafo de Interacciones y Círculo Cercano (SNA)
# ---------------------------------------------------------------------------
def get_case_sna_inputs(case_id: str) -> Optional[dict]:
    """Recopila handles del sujeto, interacciones y comentarios para el motor SNA."""
    with _connect() as conn:
        case_row = conn.execute("SELECT id, title, subject_name FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not case_row:
            return None
        ident_rows = conn.execute(
            "SELECT kind, value FROM identifiers WHERE case_id = ? AND kind = 'username'",
            (case_id,),
        ).fetchall()
        prof_rows = conn.execute(
            "SELECT username FROM profiles WHERE case_id = ? AND manual_status != 'rejected' AND username != ''",
            (case_id,),
        ).fetchall()
        inter_rows = conn.execute(
            "SELECT * FROM interactions WHERE case_id = ?",
            (case_id,),
        ).fetchall()
        comm_rows = conn.execute(
            "SELECT * FROM comments WHERE case_id = ? ORDER BY published_at DESC",
            (case_id,),
        ).fetchall()

    handles = []
    seen = set()
    for val in [r["value"] for r in ident_rows] + [r["username"] for r in prof_rows]:
        h = (val or "").strip().lstrip("@").lower()
        if h and h not in seen:
            seen.add(h)
            handles.append(h)

    interactions = []
    for r in inter_rows:
        d = dict(r)
        d["metadata"] = _loads(d.pop("metadata_json", "{}"), {})
        interactions.append(d)

    comments = []
    for r in comm_rows:
        d = dict(r)
        d["metadata"] = _loads(d.pop("metadata_json", "{}"), {})
        comments.append(d)

    rel_data = list_relational_signals(case_id) or {}
    return {
        "case_id": case_id,
        "subject_name": case_row["subject_name"] or case_row["title"] or "",
        "subject_handles": handles,
        "interactions": interactions,
        "comments": comments,
        "relational_signals": rel_data.get("signals") or [],
    }


def compute_and_save_case_sna(case_id: str, record_run: bool = False) -> Optional[dict]:
    """Ejecuta el motor SNA, guarda el grafo en `cases.sna_json` y devuelve el resultado."""
    from modules.sna_graph import build_case_sna

    inputs = get_case_sna_inputs(case_id)
    if inputs is None:
        return None
    sna = build_case_sna(inputs)
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            "UPDATE cases SET sna_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(sna, ensure_ascii=False), now, case_id),
        )
    if record_run:
        record_case_run(
            case_id=case_id,
            kind="sna:graph",
            target=f"{sna['stats']['alters_count']} vínculos · {sna['stats']['communities_count']} comunidades",
            status="done",
            folder="",
            summary=sna["stats"],
        )
    return {"case_id": case_id, **sna}



# ---------------------------------------------------------------------------
# Módulo Investigación Autorizada :: Conexiones OAuth2, Evidencias y Lenguaje Relacional
# ---------------------------------------------------------------------------
def save_platform_connection(
    platform: str,
    account_label: str,
    external_account_id: str,
    auth_type: str,
    scopes: list,
    token_encrypted: str,
    expires_at: str = "",
    conn_id: Optional[str] = None,
) -> dict:
    """Guarda o actualiza una conexión OAuth2/autorizada (tokens cifrados, nunca expuestos)."""
    cid = conn_id or uuid.uuid4().hex[:12]
    now = _now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO platform_connections (
                id, platform, account_label, external_account_id, auth_type,
                scopes_json, token_encrypted, expires_at, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                account_label = excluded.account_label,
                external_account_id = excluded.external_account_id,
                scopes_json = excluded.scopes_json,
                token_encrypted = excluded.token_encrypted,
                expires_at = excluded.expires_at,
                status = 'active',
                updated_at = excluded.updated_at
            """,
            (
                cid,
                platform.strip().lower(),
                (account_label or "").strip(),
                (external_account_id or "").strip(),
                auth_type,
                json.dumps(scopes or [], ensure_ascii=False),
                token_encrypted,
                expires_at or "",
                now,
                now,
            ),
        )
        row = conn.execute(
            """
            SELECT id, platform, account_label, external_account_id, auth_type,
                   scopes_json, expires_at, status, created_at, updated_at
            FROM platform_connections WHERE id = ?
            """,
            (cid,),
        ).fetchone()
    d = dict(row)
    d["scopes"] = _loads(d.pop("scopes_json", "[]"), [])
    return d


def list_platform_connections(platform: Optional[str] = None) -> list:
    """Lista conexiones autorizadas SIN devolver nunca `token_encrypted`."""
    with _connect() as conn:
        if platform:
            rows = conn.execute(
                """
                SELECT id, platform, account_label, external_account_id, auth_type,
                       scopes_json, expires_at, status, created_at, updated_at
                FROM platform_connections
                WHERE platform = ? AND status != 'revoked'
                ORDER BY updated_at DESC
                """,
                (platform.strip().lower(),),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, platform, account_label, external_account_id, auth_type,
                       scopes_json, expires_at, status, created_at, updated_at
                FROM platform_connections
                WHERE status != 'revoked'
                ORDER BY updated_at DESC
                """
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["scopes"] = _loads(d.pop("scopes_json", "[]"), [])
        out.append(d)
    return out


def get_platform_connection_secret(conn_id: str) -> Optional[dict]:
    """Uso exclusivo interno del backend para descifrar un token OAuth2."""
    if not conn_id:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM platform_connections WHERE id = ? AND status = 'active'",
            (conn_id,),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["scopes"] = _loads(d.pop("scopes_json", "[]"), [])
    return d


def delete_platform_connection(conn_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM platform_connections WHERE id = ?", (conn_id,))
        return cur.rowcount > 0


def get_case_evidences(case_id: str, limit: int = 120) -> Optional[dict]:
    """Devuelve trazabilidad completa: snapshots de perfil, registros de evidencia y reacciones."""
    if not case_exists(case_id):
        return None
    with _connect() as conn:
        snap_rows = conn.execute(
            "SELECT * FROM profile_snapshots WHERE case_id = ? ORDER BY captured_at DESC, id DESC LIMIT 40",
            (case_id,),
        ).fetchall()
        ev_rows = conn.execute(
            "SELECT * FROM evidence_records WHERE case_id = ? ORDER BY captured_at DESC, id DESC LIMIT ?",
            (case_id, limit),
        ).fetchall()
        rx_rows = conn.execute(
            """
            SELECT r.*, p.url AS post_url, p.content AS post_excerpt
            FROM reactions r
            LEFT JOIN posts p ON p.id = r.post_id
            WHERE r.case_id = ?
            ORDER BY r.count DESC, r.id DESC
            LIMIT 100
            """,
            (case_id,),
        ).fetchall()

    snapshots = []
    for r in snap_rows:
        d = dict(r)
        d["public_links"] = _loads(d.pop("public_links_json", "[]"), [])
        d["public_info"] = _loads(d.pop("public_info_json", "{}"), {})
        snapshots.append(d)

    reactions = []
    for r in rx_rows:
        d = dict(r)
        d["post_excerpt"] = (d.get("post_excerpt") or "")[:120]
        reactions.append(d)

    return {
        "case_id": case_id,
        "profile_snapshots": snapshots,
        "evidences": [dict(r) for r in ev_rows],
        "reactions": reactions,
    }


def save_relational_signals(case_id: str, signals: list, replace_pending: bool = True) -> dict:
    """
    Persiste señales de lenguaje relacional detectadas.
    Si `replace_pending=True`, preserva las decisiones humanas (`validated` / `dismissed` / `ambiguous`)
    y actualiza únicamente las señales aún en estado `pending` para evitar perder correcciones manuales.
    """
    if not case_exists(case_id):
        return {"inserted": 0, "preserved_reviewed": 0}
    now = _now_iso()
    inserted = 0
    with _connect() as conn:
        # Mapa rápido de evidencia por (ref_table, ref_id) para vincular `evidence_id`
        ev_map = {}
        for row in conn.execute(
            "SELECT id, ref_table, ref_id FROM evidence_records WHERE case_id = ? AND ref_id IS NOT NULL",
            (case_id,),
        ):
            ev_map[(row["ref_table"], row["ref_id"])] = row["id"]

        reviewed_keys = set()
        for row in conn.execute(
            """
            SELECT comment_id, post_id, category, matched_expression
            FROM relational_signals
            WHERE case_id = ? AND review_status != 'pending'
            """,
            (case_id,),
        ):
            reviewed_keys.add((row["comment_id"], row["post_id"], row["category"], row["matched_expression"].lower()))

        if replace_pending:
            conn.execute(
                "DELETE FROM relational_signals WHERE case_id = ? AND review_status = 'pending'",
                (case_id,),
            )

        for sig in signals or []:
            cid = sig.get("comment_id")
            pid = sig.get("post_id")
            cat = (sig.get("category") or "afectivo_general").strip().lower()
            expr = (sig.get("matched_expression") or "").strip()
            if not expr:
                continue
            dedup_key = (cid, pid, cat, expr.lower())
            if dedup_key in reviewed_keys:
                continue
            ev_id = sig.get("evidence_id")
            if not ev_id:
                if cid:
                    ev_id = ev_map.get(("comments", cid))
                elif pid:
                    ev_id = ev_map.get(("posts", pid))

            conn.execute(
                """
                INSERT INTO relational_signals (
                    case_id, evidence_id, comment_id, post_id, platform,
                    source_handle, target_handle, category, subcategory,
                    matched_expression, context_excerpt, epistemic_type,
                    ambiguity_flags_json, confidence, engine, review_status,
                    reviewer_note, reviewed_at, detected_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', '', ?)
                """,
                (
                    case_id,
                    ev_id,
                    cid,
                    pid,
                    (sig.get("platform") or "open").strip().lower(),
                    (sig.get("source_handle") or "").strip().lower(),
                    (sig.get("target_handle") or "").strip().lower(),
                    cat,
                    (sig.get("subcategory") or "").strip().lower(),
                    expr[:120],
                    (sig.get("context_excerpt") or "")[:400],
                    sig.get("epistemic_type") or "ambiguous",
                    json.dumps(sig.get("ambiguity_flags") or [], ensure_ascii=False),
                    (sig.get("confidence") or "BAJA").upper(),
                    sig.get("engine") or "local_rules",
                    now,
                ),
            )
            inserted += 1
        conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
    return {"inserted": inserted, "preserved_reviewed": len(reviewed_keys)}


def list_relational_signals(
    case_id: str,
    category: Optional[str] = None,
    epistemic_type: Optional[str] = None,
    review_status: Optional[str] = None,
    handle: Optional[str] = None,
) -> Optional[dict]:
    """Lista señales de lenguaje relacional con trazabilidad a URL de origen y métricas."""
    if not case_exists(case_id):
        return None
    clauses = ["rs.case_id = ?"]
    params = [case_id]
    if category:
        clauses.append("rs.category = ?")
        params.append(category.strip().lower())
    if epistemic_type:
        clauses.append("rs.epistemic_type = ?")
        params.append(epistemic_type.strip().lower())
    if review_status:
        clauses.append("rs.review_status = ?")
        params.append(review_status.strip().lower())
    if handle:
        h = handle.strip().lstrip("@").lower()
        clauses.append("(rs.source_handle = ? OR rs.target_handle = ?)")
        params.extend([h, h])

    where_sql = " AND ".join(clauses)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT rs.*,
                   ev.source_url AS evidence_url,
                   ev.access_method AS evidence_access_method,
                   ev.captured_at AS evidence_captured_at
            FROM relational_signals rs
            LEFT JOIN evidence_records ev ON ev.id = rs.evidence_id
            WHERE {where_sql}
            ORDER BY
                CASE rs.review_status WHEN 'validated' THEN 1 WHEN 'pending' THEN 2 WHEN 'ambiguous' THEN 3 ELSE 4 END,
                CASE rs.epistemic_type WHEN 'explicit' THEN 1 WHEN 'model_inference' THEN 2 ELSE 3 END,
                rs.id DESC
            """,
            params,
        ).fetchall()

    signals = []
    for r in rows:
        d = dict(r)
        d["ambiguity_flags"] = _loads(d.pop("ambiguity_flags_json", "[]"), [])
        signals.append(d)

    stats = {
        "total": len(signals),
        "explicit": sum(1 for s in signals if s["epistemic_type"] == "explicit"),
        "ambiguous": sum(1 for s in signals if s["epistemic_type"] == "ambiguous"),
        "model_inference": sum(1 for s in signals if s["epistemic_type"] == "model_inference"),
        "validated": sum(1 for s in signals if s["review_status"] == "validated"),
        "pending": sum(1 for s in signals if s["review_status"] == "pending"),
        "dismissed": sum(1 for s in signals if s["review_status"] == "dismissed"),
        "by_category": {},
    }
    for s in signals:
        if s["review_status"] != "dismissed":
            cat = s["category"]
            stats["by_category"][cat] = stats["by_category"].get(cat, 0) + 1

    return {"case_id": case_id, "stats": stats, "signals": signals}


def update_relational_signal_review(
    case_id: str,
    signal_id: int,
    review_status: str,
    category: Optional[str] = None,
    reviewer_note: Optional[str] = None,
) -> Optional[dict]:
    """Permite al analista validar, marcar como ambigua o descartar una señal relacional."""
    valid_st = {"pending", "validated", "ambiguous", "dismissed"}
    st = (review_status or "").strip().lower()
    if st not in valid_st:
        return None
    now = _now_iso()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM relational_signals WHERE id = ? AND case_id = ?",
            (signal_id, case_id),
        ).fetchone()
        if not row:
            return None
        new_cat = (category or row["category"]).strip().lower()
        new_note = reviewer_note.strip() if reviewer_note is not None else (row["reviewer_note"] or "")
        conn.execute(
            """
            UPDATE relational_signals
            SET review_status = ?, category = ?, reviewer_note = ?, reviewed_at = ?
            WHERE id = ? AND case_id = ?
            """,
            (st, new_cat, new_note[:300], now, signal_id, case_id),
        )
        conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
        updated = conn.execute(
            """
            SELECT rs.*, ev.source_url AS evidence_url, ev.access_method AS evidence_access_method
            FROM relational_signals rs
            LEFT JOIN evidence_records ev ON ev.id = rs.evidence_id
            WHERE rs.id = ?
            """,
            (signal_id,),
        ).fetchone()
    d = dict(updated)
    d["ambiguity_flags"] = _loads(d.pop("ambiguity_flags_json", "[]"), [])
    return d


