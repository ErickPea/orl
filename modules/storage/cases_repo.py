# -*- coding: utf-8 -*-
"""Repositorio de casos, entidades, identificadores, perfiles y jobs."""
import json
import re
import shutil
import sqlite3
import uuid
from typing import Optional

from .db import (
    VALID_IDENTIFIER_KINDS,
    VALID_MANUAL_STATUS,
    VALID_PURPOSES,
    _connect,
    _loads,
    _normalize_identifier,
    _now_iso,
)
from modules import utils
from modules.entity_resolution import evaluate_case_resolution, normalize_text, normalize_url

# ---------------------------------------------------------------------------
# Gestión de Casos y Entidad Principal
# ---------------------------------------------------------------------------
# Para qué sirve: Crea un nuevo caso OSINT junto con su entidad principal y semillas iniciales opcionales.
# Cómo funciona: Inserta en `cases` y `entities`, registra identificadores iniciales con `add_identifier`, recalcula resolución y retorna el detalle.
def create_case(
    title: str,
    subject_name: str = "",
    purpose: str = "autodiagnostico",
    notes: str = "",
    seeds: Optional[dict] = None,
) -> dict:
    """Crea un caso junto con su entidad principal y semillas opcionales."""
    now = _now_iso()
    case_id = uuid.uuid4().hex[:12]
    entity_id = uuid.uuid4().hex[:12]
    clean_title = (title or "").strip() or "Caso sin título"
    clean_subject = (subject_name or "").strip() or clean_title
    clean_purpose = purpose if purpose in VALID_PURPOSES else "otro"

    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO cases (id, title, subject_name, purpose, notes, status, resolution_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'open', '{}', ?, ?)
            """,
            (case_id, clean_title, clean_subject, clean_purpose, (notes or "").strip(), now, now),
        )
        conn.execute(
            """
            INSERT INTO entities (id, case_id, canonical_name, entity_type, is_primary, summary_json, created_at, updated_at)
            VALUES (?, ?, ?, 'person', 1, '{}', ?, ?)
            """,
            (entity_id, case_id, clean_subject, now, now),
        )

    # Semillas iniciales opcionales
    if subject_name and subject_name.strip():
        add_identifier(case_id, "full_name", subject_name.strip(), source="caso", confidence="ALTA", recalc=False)
    for kind, key in (
        ("username", "username"),
        ("email", "email"),
        ("phone", "phone"),
        ("location", "location"),
        ("domain", "domain"),
    ):
        val = ((seeds or {}).get(key) or "").strip()
        if val:
            add_identifier(case_id, kind, val, source="caso", confidence="ALTA", recalc=False)

    recalculate_case_resolution(case_id)
    return get_case_detail(case_id)


# Para qué sirve: Obtiene el ID de la entidad principal asociada a un caso.
# Cómo funciona: Consulta `entities` ordenando por `is_primary DESC, created_at ASC` dentro de la conexión dada.
def _get_primary_entity_id(conn: sqlite3.Connection, case_id: str) -> Optional[str]:
    row = conn.execute(
        "SELECT id FROM entities WHERE case_id = ? ORDER BY is_primary DESC, created_at ASC LIMIT 1",
        (case_id,),
    ).fetchone()
    return row["id"] if row else None


# Para qué sirve: Verifica rápidamente si un `case_id` existe en la tabla `cases`.
# Cómo funciona: Ejecuta `SELECT 1 FROM cases WHERE id = ?` y devuelve `True` si obtiene fila.
def case_exists(case_id: str) -> bool:
    """Comprueba si el caso existe en SQLite."""
    if not case_id:
        return False
    with _connect() as conn:
        row = conn.execute("SELECT 1 FROM cases WHERE id = ?", (case_id,)).fetchone()
        return row is not None


# Para qué sirve: Lista todos los casos registrados junto con sus contadores de evidencia y perfiles.
# Cómo funciona: Ejecuta un `SELECT` con subconsultas `COUNT(*)` por tabla hija y decodifica `resolution_json`.
def list_cases() -> list:
    """Devuelve todos los casos ordenados por actualización reciente con métricas agregadas."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT
                c.*,
                (SELECT COUNT(*) FROM identifiers i WHERE i.case_id = c.id) AS identifiers_count,
                (SELECT COUNT(*) FROM profiles p WHERE p.case_id = c.id) AS profiles_count,
                (SELECT COUNT(*) FROM profiles p WHERE p.case_id = c.id AND p.resolution_level = 'VERIFICADO') AS verified_count,
                (SELECT COUNT(*) FROM profiles p WHERE p.case_id = c.id AND p.resolution_level = 'REVISAR') AS review_count,
                (SELECT COUNT(*) FROM media_items m WHERE m.case_id = c.id) AS media_count,
                (SELECT COUNT(*) FROM posts po WHERE po.case_id = c.id) AS posts_count,
                (SELECT COUNT(*) FROM comments co WHERE co.case_id = c.id) AS comments_count,
                (SELECT COUNT(*) FROM interactions ix WHERE ix.case_id = c.id) AS interactions_count,
                (SELECT COUNT(*) FROM case_runs r WHERE r.case_id = c.id) AS runs_count
            FROM cases c
            ORDER BY c.updated_at DESC, c.created_at DESC
            """
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["resolution"] = _loads(d.pop("resolution_json", "{}"), {})
        out.append(d)
    return out


# Para qué sirve: Devuelve la cantidad total de casos almacenados en SQLite.
# Cómo funciona: Ejecuta `SELECT COUNT(*) AS n FROM cases` y retorna el entero resultante.
def count_cases() -> int:
    """Devuelve el número total de casos registrados."""
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM cases").fetchone()
        return int(row["n"]) if row else 0


# Para qué sirve: Actualiza metadatos, estado o ámbito de amigos de un caso existente.
# Cómo funciona: Construye dinámicamente el `UPDATE cases`, sincroniza `entities.canonical_name` si cambia el sujeto y recalcula resolución.
def update_case(
    case_id: str,
    title: Optional[str] = None,
    subject_name: Optional[str] = None,
    purpose: Optional[str] = None,
    notes: Optional[str] = None,
    status: Optional[str] = None,
    friend_scope_enabled: Optional[bool] = None,
) -> Optional[dict]:
    """Actualiza metadatos y banderas del caso (incluida `friend_scope_enabled`) y recalcula resolución."""
    if not case_exists(case_id):
        return None
    updates = []
    params = []
    if title is not None and title.strip():
        updates.append("title = ?")
        params.append(title.strip())
    if subject_name is not None:
        updates.append("subject_name = ?")
        params.append(subject_name.strip())
    if purpose is not None and purpose in VALID_PURPOSES:
        updates.append("purpose = ?")
        params.append(purpose)
    if notes is not None:
        updates.append("notes = ?")
        params.append(notes.strip())
    if status in ("open", "closed"):
        updates.append("status = ?")
        params.append(status)
    if friend_scope_enabled is not None:
        updates.append("friend_scope_enabled = ?")
        params.append(1 if friend_scope_enabled else 0)

    if updates:
        now = _now_iso()
        updates.append("updated_at = ?")
        params.append(now)
        params.append(case_id)
        with _connect() as conn:
            conn.execute(f"UPDATE cases SET {', '.join(updates)} WHERE id = ?", params)
            if subject_name is not None and subject_name.strip():
                conn.execute(
                    "UPDATE entities SET canonical_name = ?, updated_at = ? WHERE case_id = ? AND is_primary = 1",
                    (subject_name.strip(), now, case_id),
                )
        recalculate_case_resolution(case_id)

    return get_case_detail(case_id)


# Para qué sirve: Elimina un caso en cascada de SQLite y opcionalmente borra sus carpetas en `OUTPUT_ROOT`.
# Cómo funciona: Recolecta carpetas referenciadas, borra la fila en `cases` (cascada FK) y aplica `shutil.rmtree` validando que sigan dentro de `OUTPUT_ROOT`.
def delete_case(case_id: str, delete_files: bool = False) -> dict:
    """
    Elimina un caso completo en cascada en SQLite.
    Si `delete_files=True`, borra además únicamente las carpetas dentro de `OUTPUT_ROOT`
    asociadas a ese caso (validación estricta contra path traversal).
    """
    if not case_exists(case_id):
        return {"deleted": False, "deleted_folders": []}

    folders = set()
    with _connect() as conn:
        for row in conn.execute("SELECT folder FROM case_runs WHERE case_id = ? AND folder != ''", (case_id,)):
            folders.add(row["folder"])
        for row in conn.execute("SELECT folder FROM media_items WHERE case_id = ? AND folder != ''", (case_id,)):
            folders.add(row["folder"])
        conn.execute("DELETE FROM cases WHERE id = ?", (case_id,))

    deleted_folders = []
    if delete_files and folders:
        base = utils.OUTPUT_ROOT.resolve()
        for folder in folders:
            target = (base / folder).resolve()
            if target.parent == base and target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
                deleted_folders.append(folder)

    return {"deleted": True, "deleted_folders": deleted_folders}


# Para qué sirve: Construye la vista completa consolidada de un caso OSINT.
# Cómo funciona: Consulta caso, entidades, identificadores, perfiles ordenados, medios, bitácora y contadores de contenido social en SQLite.
def get_case_detail(case_id: str) -> Optional[dict]:
    """Devuelve toda la vista consolidada de un caso."""
    with _connect() as conn:
        case_row = conn.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not case_row:
            return None
        entity_rows = conn.execute(
            "SELECT * FROM entities WHERE case_id = ? ORDER BY is_primary DESC, created_at ASC",
            (case_id,),
        ).fetchall()
        ident_rows = conn.execute(
            "SELECT * FROM identifiers WHERE case_id = ? ORDER BY kind ASC, created_at DESC",
            (case_id,),
        ).fetchall()
        profile_rows = conn.execute(
            """
            SELECT * FROM profiles
            WHERE case_id = ?
            ORDER BY
                CASE resolution_level
                    WHEN 'VERIFICADO' THEN 1
                    WHEN 'PROBABLE' THEN 2
                    WHEN 'REVISAR' THEN 3
                    ELSE 4
                END,
                resolution_score DESC,
                site ASC
            """,
            (case_id,),
        ).fetchall()
        media_rows = conn.execute(
            "SELECT * FROM media_items WHERE case_id = ? ORDER BY created_at DESC",
            (case_id,),
        ).fetchall()
        run_rows = conn.execute(
            "SELECT * FROM case_runs WHERE case_id = ? ORDER BY created_at DESC LIMIT 50",
            (case_id,),
        ).fetchall()
        posts_count = int(conn.execute("SELECT COUNT(*) AS n FROM posts WHERE case_id = ?", (case_id,)).fetchone()["n"])
        comments_count = int(conn.execute("SELECT COUNT(*) AS n FROM comments WHERE case_id = ?", (case_id,)).fetchone()["n"])
        interactions_count = int(conn.execute("SELECT COUNT(*) AS n FROM interactions WHERE case_id = ?", (case_id,)).fetchone()["n"])

    case_dict = dict(case_row)
    resolution = _loads(case_dict.pop("resolution_json", "{}"), {})

    profiles = []
    for r in profile_rows:
        d = dict(r)
        d["resolution_reasons"] = _loads(d.get("resolution_reasons"), [])
        d["metadata"] = _loads(d.pop("metadata_json", "{}"), {})
        profiles.append(d)

    media_items = []
    for r in media_rows:
        d = dict(r)
        d["metadata"] = _loads(d.pop("metadata_json", "{}"), {})
        d["tags"] = _loads(d.pop("tags_json", "[]"), [])
        media_items.append(d)

    runs = []
    for r in run_rows:
        d = dict(r)
        d["summary"] = _loads(d.pop("summary_json", "{}"), {})
        runs.append(d)

    entities = []
    for r in entity_rows:
        d = dict(r)
        d["summary"] = _loads(d.pop("summary_json", "{}"), {})
        entities.append(d)

    return {
        "case": case_dict,
        "entities": entities,
        "identifiers": [dict(r) for r in ident_rows],
        "profiles": profiles,
        "media_items": media_items,
        "runs": runs,
        "resolution": resolution,
        "content_counts": {
            "posts": posts_count,
            "comments": comments_count,
            "media": len(media_items),
            "interactions": interactions_count,
        },
    }


# ---------------------------------------------------------------------------
# Identificadores y Perfiles
# ---------------------------------------------------------------------------
# Para qué sirve: Agrega o actualiza un identificador semilla en el caso deduplicando por su forma normalizada.
# Cómo funciona: Normaliza según `kind`, ejecuta upsert sobre `identifiers` y dispara recálculo de resolución si `recalc=True`.
def add_identifier(
    case_id: str,
    kind: str,
    value: str,
    source: str = "manual",
    confidence: str = "ALTA",
    recalc: bool = True,
) -> Optional[dict]:
    """Agrega o deduplica un identificador semilla del caso (username, email, teléfono, etc.)."""
    clean_kind = (kind or "other").strip().lower()
    if clean_kind not in VALID_IDENTIFIER_KINDS:
        clean_kind = "other"
    clean_val = (value or "").strip()
    if not clean_val:
        return None
    norm = _normalize_identifier(clean_kind, clean_val)
    now = _now_iso()

    with _connect() as conn:
        entity_id = _get_primary_entity_id(conn, case_id)
        if not entity_id:
            return None
        conn.execute(
            """
            INSERT INTO identifiers (case_id, entity_id, kind, value, normalized, source, confidence, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(case_id, entity_id, kind, normalized)
            DO UPDATE SET value = excluded.value, source = excluded.source
            """,
            (case_id, entity_id, clean_kind, clean_val, norm, source, confidence, now),
        )
        conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
        row = conn.execute(
            "SELECT * FROM identifiers WHERE case_id = ? AND entity_id = ? AND kind = ? AND normalized = ?",
            (case_id, entity_id, clean_kind, norm),
        ).fetchone()

    if recalc:
        recalculate_case_resolution(case_id)
    return dict(row) if row else None


# Para qué sirve: Elimina un identificador del caso y actualiza la resolución de entidades asociada.
# Cómo funciona: Ejecuta `DELETE FROM identifiers`, refresca `cases.updated_at` y llama a `recalculate_case_resolution`.
def delete_identifier(case_id: str, identifier_id: int) -> bool:
    """Elimina un identificador del caso y recalcula la resolución de entidades."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM identifiers WHERE id = ? AND case_id = ?",
            (identifier_id, case_id),
        )
        deleted = cur.rowcount > 0
        if deleted:
            conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (_now_iso(), case_id))
    if deleted:
        recalculate_case_resolution(case_id)
    return deleted


# Para qué sirve: Combina cadenas de fuentes de hallazgos sin repetir nombres.
# Cómo funciona: Divide `existing` e `incoming` por `+` y `,` preservando orden y unicidad insensible a mayúsculas.
def _merge_sources(existing: str, incoming: str) -> str:
    parts = []
    seen = set()
    for chunk in (existing or "", incoming or ""):
        for item in chunk.replace("+", ",").split(","):
            s = item.strip()
            if s and s.lower() not in seen:
                seen.add(s.lower())
                parts.append(s)
    return "+".join(parts)


# Para qué sirve: Selecciona el nivel de confianza OSINT mayor entre dos valores dados.
# Cómo funciona: Compara ambos niveles con un diccionario de prioridades (`ALTA` > `MEDIA` > `BAJA`).
def _best_confidence(a: str, b: str) -> str:
    rank = {"ALTA": 3, "MEDIA": 2, "BAJA": 1}
    ua = (a or "BAJA").upper()
    ub = (b or "BAJA").upper()
    return ua if rank.get(ua, 0) >= rank.get(ub, 0) else ub


# Para qué sirve: Inserta o enriquece un perfil encontrado dentro del caso evitando duplicados por URL.
# Cómo funciona: Normaliza la URL, fusiona metadatos/fuentes/confianza si ya existe o inserta en `profiles` y recalcula si `recalc=True`.
def upsert_profile(
    case_id: str,
    site: str,
    url: str,
    username: str = "",
    display_name: str = "",
    bio: str = "",
    location: str = "",
    avatar_url: str = "",
    website: str = "",
    sources: str = "manual",
    osint_confidence: str = "MEDIA",
    metadata: Optional[dict] = None,
    recalc: bool = False,
) -> Optional[dict]:
    """Inserta o enriquece un perfil dentro de un caso deduplicando por URL normalizada."""
    clean_url = (url or "").strip()
    norm_u = normalize_url(clean_url)
    if not norm_u:
        return None
    now = _now_iso()

    with _connect() as conn:
        entity_id = _get_primary_entity_id(conn, case_id)
        if not entity_id:
            return None
        existing = conn.execute(
            "SELECT * FROM profiles WHERE case_id = ? AND normalized_url = ?",
            (case_id, norm_u),
        ).fetchone()

        if existing:
            prev_meta = _loads(existing["metadata_json"], {})
            merged_meta = {**prev_meta, **(metadata or {})}
            conn.execute(
                """
                UPDATE profiles SET
                    site = ?,
                    username = ?,
                    display_name = ?,
                    bio = ?,
                    location = ?,
                    avatar_url = ?,
                    website = ?,
                    sources = ?,
                    osint_confidence = ?,
                    metadata_json = ?,
                    last_seen_at = ?
                WHERE id = ?
                """,
                (
                    site.strip() or existing["site"],
                    (username or "").strip() or existing["username"],
                    (display_name or "").strip() or existing["display_name"],
                    (bio or "").strip() or existing["bio"],
                    (location or "").strip() or existing["location"],
                    (avatar_url or "").strip() or existing["avatar_url"],
                    (website or "").strip() or existing["website"],
                    _merge_sources(existing["sources"], sources),
                    _best_confidence(existing["osint_confidence"], osint_confidence),
                    json.dumps(merged_meta, ensure_ascii=False),
                    now,
                    existing["id"],
                ),
            )
            pid = existing["id"]
        else:
            cur = conn.execute(
                """
                INSERT INTO profiles (
                    case_id, entity_id, site, url, normalized_url, username, display_name,
                    bio, location, avatar_url, website, sources, osint_confidence,
                    metadata_json, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    entity_id,
                    (site or "Sitio").strip(),
                    clean_url,
                    norm_u,
                    (username or "").strip(),
                    (display_name or "").strip(),
                    (bio or "").strip(),
                    (location or "").strip(),
                    (avatar_url or "").strip(),
                    (website or "").strip(),
                    (sources or "manual").strip(),
                    (osint_confidence or "MEDIA").upper(),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            pid = cur.lastrowid

        conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
        row = conn.execute("SELECT * FROM profiles WHERE id = ?", (pid,)).fetchone()

    if recalc:
        recalculate_case_resolution(case_id)
    return dict(row) if row else None


# Para qué sirve: Permite al analista confirmar, rechazar o devolver a automático el estado de un perfil del caso.
# Cómo funciona: Valida `manual_status` contra `VALID_MANUAL_STATUS`, actualiza `profiles` y recalcula la resolución del caso.
def set_profile_manual_status(case_id: str, profile_id: int, manual_status: str) -> Optional[dict]:
    """Permite al analista confirmar, descartar o regresar a evaluación automática un perfil."""
    status = (manual_status or "").strip().lower()
    if status not in VALID_MANUAL_STATUS:
        return None
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE profiles SET manual_status = ?, last_seen_at = ? WHERE id = ? AND case_id = ?",
            (status, _now_iso(), profile_id, case_id),
        )
        if cur.rowcount == 0:
            return None
        conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (_now_iso(), case_id))
    return recalculate_case_resolution(case_id)


# ---------------------------------------------------------------------------
# Recálculo de Resolución de Entidades
# ---------------------------------------------------------------------------
# Para qué sirve: Recalcula los puntajes y niveles de resolución de identidad de todos los perfiles de un caso.
# Cómo funciona: Obtiene identificadores y perfiles, ejecuta `evaluate_case_resolution` y actualiza `profiles` y `cases.resolution_json`.
def recalculate_case_resolution(case_id: str) -> Optional[dict]:
    """Ejecuta el motor de Entity Resolution sobre todos los perfiles del caso y persiste resultados."""
    with _connect() as conn:
        case_row = conn.execute("SELECT id, subject_name FROM cases WHERE id = ?", (case_id,)).fetchone()
        if not case_row:
            return None
        ident_rows = [dict(r) for r in conn.execute("SELECT * FROM identifiers WHERE case_id = ?", (case_id,)).fetchall()]
        profile_rows = [dict(r) for r in conn.execute("SELECT * FROM profiles WHERE case_id = ?", (case_id,)).fetchall()]

    res = evaluate_case_resolution(
        profiles=profile_rows,
        identifiers=ident_rows,
        subject_name=case_row["subject_name"] or "",
    )

    now = _now_iso()
    summary_payload = {
        "stats": res["stats"],
        "cross_links": res["cross_links"],
        "warnings": res["warnings"],
        "evaluated_at": now,
    }

    with _connect() as conn:
        for ev in res["profiles"]:
            conn.execute(
                """
                UPDATE profiles
                SET resolution_score = ?, resolution_level = ?, resolution_reasons = ?
                WHERE id = ? AND case_id = ?
                """,
                (
                    ev["score"],
                    ev["level"],
                    json.dumps(ev["reasons"], ensure_ascii=False),
                    ev["profile_id"],
                    case_id,
                ),
            )
        conn.execute(
            "UPDATE cases SET resolution_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(summary_payload, ensure_ascii=False), now, case_id),
        )

    return summary_payload


# ---------------------------------------------------------------------------
# Registro de Media y Ejecuciones (Runs)
# ---------------------------------------------------------------------------
# Para qué sirve: Registra un archivo multimedia inspeccionado (EXIF, GPS, hashes y copia limpia) en un caso.
# Cómo funciona: Extrae coordenadas, hashes y dispositivo de `media_result` e inserta una fila en `media_items`.
def add_media_item(
    case_id: str,
    media_result: dict,
    folder: str = "",
    clean_copy_url: str = "",
    local_path: str = "",
) -> Optional[dict]:
    """Registra metadatos EXIF/GPS/hashes de un archivo multimedia inspeccionado para el caso."""
    if not case_exists(case_id):
        return None
    now = _now_iso()
    gps = media_result.get("gps") or {}
    hashes = media_result.get("hashes") or {}
    privacy = media_result.get("privacy") or {}
    device_parts = [p for p in (media_result.get("device"), media_result.get("camera")) if p]
    device = " · ".join(device_parts) or ", ".join(privacy.get("exposed_fields") or [])[:120]

    with _connect() as conn:
        entity_id = _get_primary_entity_id(conn, case_id)
        cur = conn.execute(
            """
            INSERT INTO media_items (
                case_id, entity_id, filename, media_type, sha256, md5, file_size,
                gps_lat, gps_lon, captured_date, device, folder, clean_copy_url,
                local_path, metadata_json, tags_json, vision_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', '{}', ?)
            """,
            (
                case_id,
                entity_id,
                media_result.get("filename") or "archivo",
                media_result.get("kind") or "image",
                hashes.get("sha256") or "",
                hashes.get("md5") or "",
                int(media_result.get("size_bytes") or 0),
                gps.get("latitude"),
                gps.get("longitude"),
                media_result.get("date_taken") or "",
                device,
                folder or "",
                clean_copy_url or "",
                local_path or "",
                json.dumps(media_result, ensure_ascii=False),
                now,
            ),
        )
        conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
        row = conn.execute("SELECT * FROM media_items WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row) if row else None


# Para qué sirve: Anota una ejecución u operación auditada dentro de la bitácora `case_runs`.
# Cómo funciona: Inserta tipo, objetivo, estado, carpeta y resumen JSON en `case_runs` y actualiza `cases.updated_at`.
def record_case_run(
    case_id: str,
    kind: str,
    target: str,
    status: str = "done",
    folder: str = "",
    summary: Optional[dict] = None,
    job_id: str = "",
) -> Optional[dict]:
    """Registra un evento en la bitácora de auditoría `case_runs` del caso."""
    if not case_exists(case_id):
        return None
    now = _now_iso()
    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO case_runs (case_id, job_id, kind, target, status, folder, summary_json, created_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                case_id,
                job_id or "",
                kind,
                target,
                status,
                folder or "",
                json.dumps(summary or {}, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))
        row = conn.execute("SELECT * FROM case_runs WHERE id = ?", (cur.lastrowid,)).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Ingesta automática desde resultados de jobs de OSINT-Auto
# ---------------------------------------------------------------------------
# Para qué sirve: Normaliza un diccionario heterogéneo de perfil a los campos esperados por `upsert_profile`.
# Cómo funciona: Combina `public_meta`, `metadata` y claves legacy para devolver sitio, URL, handle, bio, ubicación y confianza.
def _extract_profile_fields(p: dict, fallback_username: str = "") -> dict:
    """Extrae metadatos normalizados de un perfil generado por reports/social_enrich/name_search."""
    pm = p.get("public_meta") or {}
    meta_dict = p.get("metadata") if isinstance(p.get("metadata"), dict) else {}

    site = p.get("sitio") or p.get("network") or p.get("site") or "Sitio"
    url = p.get("url") or ""
    username = p.get("username") or pm.get("username") or fallback_username or ""
    display_name = (
        pm.get("name")
        or pm.get("title")
        or p.get("person_name")
        or meta_dict.get("fullname")
        or meta_dict.get("name")
        or ""
    )
    bio = (
        pm.get("bio")
        or pm.get("description")
        or p.get("bio")
        or meta_dict.get("bio")
        or p.get("metadata_resumen")
        or ""
    )
    location = pm.get("location") or p.get("location") or meta_dict.get("location") or ""
    avatar_url = pm.get("avatar") or pm.get("image") or p.get("avatar_url") or ""
    website = pm.get("website") or pm.get("blog") or p.get("website") or ""
    sources = p.get("fuentes") or p.get("source") or "OSINT"
    conf = p.get("confianza") or p.get("match") or "MEDIA"

    extra_meta = {}
    if pm:
        extra_meta["public_meta"] = pm
    if p.get("metadata_resumen"):
        extra_meta["metadata_resumen"] = p.get("metadata_resumen")
    if p.get("tags"):
        extra_meta["tags"] = p.get("tags")

    return {
        "site": str(site),
        "url": str(url),
        "username": str(username),
        "display_name": str(display_name)[:200],
        "bio": str(bio)[:600],
        "location": str(location)[:160],
        "avatar_url": str(avatar_url)[:500],
        "website": str(website)[:300],
        "sources": str(sources),
        "osint_confidence": str(conf).upper(),
        "metadata": extra_meta,
    }


# Para qué sirve: Ingresa automáticamente hallazgos de cualquier job OSINT terminado dentro del caso en SQLite.
# Cómo funciona: Despacha según `kind` para agregar identificadores/perfiles sin recalcular en cada paso, registra el run y recalcula resolución al final.
def ingest_job_result_to_case(
    case_id: str,
    job_id: str,
    kind: str,
    target: str,
    folder: str,
    result: dict,
) -> Optional[dict]:
    """
    Persiste en SQLite todos los hallazgos estructurados de un job completado
    y recalcula el puntaje de Resolución de Entidades del caso.
    """
    if not case_id or not case_exists(case_id) or not isinstance(result, dict):
        return None

    rkind = result.get("kind") or kind
    profiles_added = 0
    identifiers_added = 0

    if rkind in ("username", "social", "full"):
        uname = (result.get("username") or "").strip()
        if uname:
            add_identifier(case_id, "username", uname, source=rkind, confidence="ALTA", recalc=False)
            identifiers_added += 1
        country = (result.get("country") or "").strip()
        if country:
            add_identifier(case_id, "location", country, source=rkind, confidence="MEDIA", recalc=False)
            identifiers_added += 1

        raw_profiles = result.get("social_profiles") or result.get("profiles") or []
        for p in raw_profiles:
            fields = _extract_profile_fields(p, fallback_username=uname)
            if fields["url"]:
                upsert_profile(case_id, **fields, recalc=False)
                profiles_added += 1

        # En pipeline completo también pueden venir correos y teléfono
        for email_addr in (result.get("emails") or {}).keys():
            add_identifier(case_id, "email", email_addr, source="full", confidence="MEDIA", recalc=False)
            identifiers_added += 1
        phone_res = result.get("phone") or {}
        if phone_res.get("phone"):
            add_identifier(case_id, "phone", phone_res["phone"], source="full", confidence="ALTA", recalc=False)
            identifiers_added += 1
            loc = (phone_res.get("analysis") or {}).get("location")
            if loc:
                add_identifier(case_id, "location", loc, source="phonenumbers", confidence="MEDIA", recalc=False)
                identifiers_added += 1

    elif rkind == "name":
        q = result.get("query") or {}
        if q.get("full_name"):
            add_identifier(case_id, "full_name", q["full_name"], source="name_search", confidence="ALTA", recalc=False)
            identifiers_added += 1
        loc_str = ", ".join(x for x in (q.get("city"), q.get("country")) if x)
        if loc_str:
            add_identifier(case_id, "location", loc_str, source="name_search", confidence="ALTA", recalc=False)
            identifiers_added += 1
        for c in result.get("candidates") or []:
            fields = _extract_profile_fields(c)
            if fields["url"]:
                upsert_profile(case_id, **fields, recalc=False)
                profiles_added += 1

    elif rkind == "email":
        email_addr = (result.get("email") or "").strip()
        if email_addr:
            add_identifier(case_id, "email", email_addr, source="email_search", confidence="ALTA", recalc=False)
            identifiers_added += 1
        grav = result.get("gravatar") or {}
        if grav.get("found"):
            if grav.get("preferred_username"):
                add_identifier(case_id, "username", grav["preferred_username"], source="Gravatar", confidence="ALTA", recalc=False)
                identifiers_added += 1
            if grav.get("display_name"):
                add_identifier(case_id, "full_name", grav["display_name"], source="Gravatar", confidence="ALTA", recalc=False)
                identifiers_added += 1
            if grav.get("location"):
                add_identifier(case_id, "location", grav["location"], source="Gravatar", confidence="MEDIA", recalc=False)
                identifiers_added += 1
            if grav.get("profile_url"):
                upsert_profile(
                    case_id,
                    site="Gravatar",
                    url=grav["profile_url"],
                    username=grav.get("preferred_username") or "",
                    display_name=grav.get("display_name") or "",
                    bio=grav.get("about") or "",
                    location=grav.get("location") or "",
                    avatar_url=grav.get("avatar_url") or "",
                    sources="Gravatar",
                    osint_confidence="ALTA",
                    recalc=False,
                )
                profiles_added += 1

    elif rkind == "email_generate":
        uname = (result.get("username") or "").strip()
        if uname:
            add_identifier(case_id, "username", uname, source="email_generate", confidence="ALTA", recalc=False)
            identifiers_added += 1
        for em in (result.get("found") or {}).keys():
            add_identifier(case_id, "email", em, source="Holehe", confidence="MEDIA", recalc=False)
            identifiers_added += 1

    elif rkind == "phone":
        ph = (result.get("phone") or "").strip()
        if ph:
            add_identifier(case_id, "phone", ph, source="phone_search", confidence="ALTA", recalc=False)
            identifiers_added += 1
        loc = (result.get("analysis") or {}).get("location")
        if loc:
            add_identifier(case_id, "location", loc, source="phonenumbers", confidence="MEDIA", recalc=False)
            identifiers_added += 1

    elif rkind == "breach_email":
        em = ((result.get("breaches") or {}).get("email") or target or "").strip()
        if em and "@" in em:
            add_identifier(case_id, "email", em, source="breach_check", confidence="ALTA", recalc=False)
            identifiers_added += 1

    record_case_run(
        case_id=case_id,
        job_id=job_id,
        kind=rkind,
        target=target,
        status="done",
        folder=folder,
        summary={
            "profiles_ingested": profiles_added,
            "identifiers_ingested": identifiers_added,
        },
    )
    resolution = recalculate_case_resolution(case_id)
    return {
        "case_id": case_id,
        "profiles_ingested": profiles_added,
        "identifiers_ingested": identifiers_added,
        "resolution": resolution,
    }


# ---------------------------------------------------------------------------
# Persistencia de Jobs (para sobrevivir reinicios del servidor)
# ---------------------------------------------------------------------------
# Para qué sirve: Guarda o actualiza el estado y progreso de un job asíncrono en SQLite.
# Cómo funciona: Ejecuta upsert en la tabla `jobs` serializando logs, archivos y resultado en JSON.
def save_job(job: dict) -> None:
    """Guarda o actualiza el estado de un job en SQLite."""
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id, case_id, kind, target, status, step, progress, folder,
                started_at, finished_at, error, logs_json, files_json, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                case_id = excluded.case_id,
                status = excluded.status,
                step = excluded.step,
                progress = excluded.progress,
                folder = excluded.folder,
                finished_at = excluded.finished_at,
                error = excluded.error,
                logs_json = excluded.logs_json,
                files_json = excluded.files_json,
                result_json = excluded.result_json
            """,
            (
                job["id"],
                job.get("case_id") if case_exists(job.get("case_id") or "") else None,
                job["kind"],
                job["target"],
                job["status"],
                job.get("step") or "",
                int(job.get("progress") or 0),
                job.get("folder") or "",
                job["started_at"],
                job.get("finished_at"),
                job.get("error"),
                json.dumps(job.get("logs") or [], ensure_ascii=False),
                json.dumps(job.get("files") or [], ensure_ascii=False),
                json.dumps(job.get("result"), ensure_ascii=False) if job.get("result") is not None else None,
            ),
        )


# Para qué sirve: Lista los jobs persistidos más recientes sin cargar su payload completo `result_json`.
# Cómo funciona: Selecciona columnas resumidas de `jobs` ordenadas por `started_at DESC` con límite configurable.
def list_persisted_jobs(limit: int = 25) -> list:
    """Lista los últimos trabajos persistidos en SQLite ordenados por fecha descendente."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT id, case_id, kind, target, status, step, progress, folder,
                   started_at, finished_at, error, logs_json, files_json
            FROM jobs
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["logs"] = _loads(d.pop("logs_json", "[]"), [])
        d["files"] = _loads(d.pop("files_json", "[]"), [])
        out.append(d)
    return out


# Para qué sirve: Recupera el detalle completo de un job persistido incluyendo logs, archivos y resultado.
# Cómo funciona: Consulta `SELECT * FROM jobs WHERE id = ?` y decodifica `logs_json`, `files_json` y `result_json`.
def get_persisted_job(job_id: str) -> Optional[dict]:
    """Recupera el detalle completo (logs, archivos y resultado) de un job persistido."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["logs"] = _loads(d.pop("logs_json", "[]"), [])
    d["files"] = _loads(d.pop("files_json", "[]"), [])
    d["result"] = _loads(d.pop("result_json", None), None)
    return d


# Para qué sirve: Marca como fallidos los jobs que quedaron en estado `running` tras un reinicio del servidor.
# Cómo funciona: Busca filas `running` en `jobs`, agrega motivo al log y las actualiza a `error` con progreso 100.
def recover_orphaned_jobs(reason: str = "Interrumpido por reinicio o cierre inesperado del servidor.") -> int:
    """
    Marca como `error` los jobs que quedaron en estado `running` en SQLite tras un reinicio,
    evitando que aparezcan activos indefinidamente. Devuelve cuántos fueron recuperados.
    """
    now = _now_iso()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, logs_json FROM jobs WHERE status = 'running'"
        ).fetchall()
        for r in rows:
            logs = _loads(r["logs_json"], [])
            if not isinstance(logs, list):
                logs = []
            logs.append(f"[{now}] {reason}")
            conn.execute(
                """
                UPDATE jobs
                SET status = 'error',
                    step = 'Interrumpido por reinicio',
                    progress = 100,
                    finished_at = ?,
                    error = ?,
                    logs_json = ?
                WHERE id = ?
                """,
                (now, reason, json.dumps(logs, ensure_ascii=False), r["id"]),
            )
    return len(rows)


