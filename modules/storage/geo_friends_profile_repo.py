# -*- coding: utf-8 -*-
"""Repositorio de geolocalización visual, historial de ubicaciones, amigos aislados y perfil del sujeto."""
import json
import re
import uuid
from typing import Optional

from .db import _connect, _loads, _now_iso
from .cases_repo import case_exists, get_case_detail, record_case_run
from .analysis_repo import compute_and_save_case_sna
from modules.logger import get_logger

_LOGGER = get_logger("storage.geo_friends")

# Para qué sirve: Reemplaza y guarda en SQLite las pistas visuales o EXIF de ubicación detectadas en una imagen.
# Cómo funciona: Borra pistas previas de `(case_id, media_id)` e inserta cada diccionario de `hints` en `geo_hints`.
def save_media_geo_hints(case_id: str, media_id: int, hints: list) -> list:
    """Reemplaza y persiste `geo_hints` para un media_id concreto del caso."""
    if not case_exists(case_id):
        return []
    now = _now_iso()
    with _connect() as conn:
        conn.execute("DELETE FROM geo_hints WHERE case_id = ? AND media_id = ?", (case_id, media_id))
        saved = []
        for h in hints or []:
            cur = conn.execute(
                """
                INSERT INTO geo_hints (case_id, media_id, hint_type, value, confidence, evidence_quote, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    media_id,
                    str(h.get("hint_type") or "object")[:40],
                    str(h.get("value") or "")[:300],
                    round(max(0.0, min(1.0, float(h.get("confidence") or 0.0))), 3),
                    str(h.get("evidence_quote") or "")[:400],
                    str(h.get("source") or "local_context")[:40],
                    now,
                ),
            )
            saved.append({"id": cur.lastrowid, "media_id": media_id, **h, "created_at": now})
    return saved


# Para qué sirve: Lista todas las pistas geolocalizables (`geo_hints`) asociadas a los medios de un caso.
# Cómo funciona: Realiza `LEFT JOIN` entre `geo_hints` y `media_items` ordenando por confianza descendente.
def list_case_geo_hints(case_id: str) -> list:
    """Lista todas las pistas visuales/EXIF de ubicación (`geo_hints`) guardadas para el caso."""
    if not case_exists(case_id):
        return []
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT gh.*, m.filename, m.source_url, m.local_path
            FROM geo_hints gh
            LEFT JOIN media_items m ON m.id = gh.media_id
            WHERE gh.case_id = ?
            ORDER BY gh.confidence DESC, gh.id ASC
            """,
            (case_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Fases 0, 1, 2, 4 :: Historial de Ubicaciones, Amigos Aislados y Perfil del Sujeto
# ---------------------------------------------------------------------------
# Para qué sirve: Obtiene un elemento multimedia individual de un caso con sus metadatos decodificados.
# Cómo funciona: Consulta `media_items` por `(case_id, media_id)` y parsea `metadata_json` y `vision_json`.
def get_media_item(case_id: str, media_id: int) -> Optional[dict]:
    """Recupera un archivo multimedia concreto del caso con su metadata parseada."""
    if not case_exists(case_id):
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM media_items WHERE case_id = ? AND id = ?",
            (case_id, media_id),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["metadata"] = _loads(d.pop("metadata_json", "{}"), {})
    d["vision"] = _loads(d.pop("vision_json", "{}"), {})
    return d


# Para qué sirve: Persiste un punto de ubicación en `location_history` respetando correcciones manuales previas.
# Cómo funciona: Resuelve coordenadas faltantes si aplica, reemplaza la entrada automática o manual correspondiente e inserta auditoría si es override.
def save_location_history_entry(
    case_id: str,
    media_id: Optional[int] = None,
    owner_type: str = "subject",
    owner_id: Optional[int] = None,
    source: str = "local_context",
    country: str = "",
    region: str = "",
    city: str = "",
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    confidence: float = 0.5,
    evidence_quote: str = "",
    published_at: str = "",
    is_manual_override: bool = False,
) -> Optional[dict]:
    """Persiste una entrada en `location_history` respetando precedencia del override manual."""
    if not case_exists(case_id):
        return None
    if (lat is None or lon is None) and (city or region or country):
        try:
            from modules.geo_inference import _resolve_candidate_coords
            r_lat, r_lon, _ = _resolve_candidate_coords(
                ", ".join(x for x in (city, region, country) if x),
                country,
                region,
                city,
                is_exif=False,
            )
            if lat is None:
                lat = r_lat
            if lon is None:
                lon = r_lon
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Fallo resolviendo coordenadas para (%s, %s, %s): %s", city, region, country, exc)
    now = _now_iso()
    with _connect() as conn:
        if media_id and not is_manual_override:
            # No sobrescribir si ya existe un override manual para esta foto
            man = conn.execute(
                "SELECT id FROM location_history WHERE case_id = ? AND media_id = ? AND is_manual_override = 1",
                (case_id, media_id),
            ).fetchone()
            if man:
                conn.execute(
                    "DELETE FROM location_history WHERE case_id = ? AND media_id = ? AND is_manual_override = 0",
                    (case_id, media_id),
                )
        if media_id and is_manual_override:
            conn.execute(
                "DELETE FROM location_history WHERE case_id = ? AND media_id = ? AND is_manual_override = 1",
                (case_id, media_id),
            )
        elif media_id and not is_manual_override:
            conn.execute(
                "DELETE FROM location_history WHERE case_id = ? AND media_id = ? AND is_manual_override = 0",
                (case_id, media_id),
            )
        cur = conn.execute(
            """
            INSERT INTO location_history (
                case_id, media_id, owner_type, owner_id, source, country, region, city,
                lat, lon, confidence, evidence_quote, published_at, inferred_at, is_manual_override
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                case_id,
                media_id,
                owner_type or "subject",
                owner_id,
                source,
                country or "",
                region or "",
                city or "",
                lat,
                lon,
                round(max(0.0, min(1.0, float(confidence))), 3),
                (evidence_quote or "")[:400],
                published_at or "",
                now,
                1 if is_manual_override else 0,
            ),
        )
        row = conn.execute("SELECT * FROM location_history WHERE id = ?", (cur.lastrowid,)).fetchone()
    if is_manual_override:
        record_case_run(
            case_id,
            kind="audit:manual_location",
            target=f"media:{media_id}",
            status="done",
            summary={"country": country, "region": region, "city": city, "lat": lat, "lon": lon},
        )
    return dict(row) if row else None


# Para qué sirve: Lista la línea temporal de ubicaciones del sujeto o de un amigo aplicando filtros y precedencia manual.
# Cómo funciona: Ejecuta consulta parametrizada sobre `location_history` unida a `media_items` y prioriza `is_manual_override` por foto.
def list_location_history(
    case_id: str,
    owner_type: str = "subject",
    owner_id: Optional[int] = None,
    date_from: str = "",
    date_to: str = "",
    min_confidence: float = 0.0,
    source: str = "",
) -> list:
    """Devuelve el historial cronológico de ubicaciones filtrando por dueño, fechas, fuente y confianza."""
    if not case_exists(case_id):
        return []
    clauses = ["lh.case_id = ?", "COALESCE(lh.owner_type, 'subject') = ?"]
    params: list = [case_id, owner_type or "subject"]
    if owner_id is not None:
        clauses.append("lh.owner_id = ?")
        params.append(owner_id)
    if min_confidence and float(min_confidence) > 0:
        clauses.append("lh.confidence >= ?")
        params.append(float(min_confidence))
    if source:
        clauses.append("LOWER(lh.source) = ?")
        params.append(source.strip().lower())
    if date_from:
        clauses.append("COALESCE(NULLIF(lh.published_at, ''), lh.inferred_at) >= ?")
        params.append(date_from.strip())
    if date_to:
        clauses.append("COALESCE(NULLIF(lh.published_at, ''), lh.inferred_at) <= ?")
        params.append(date_to.strip() + "z")

    where_sql = " AND ".join(clauses)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT lh.*, m.filename, m.source_url, m.local_path, m.alt_text,
                   COALESCE(NULLIF(lh.published_at, ''), NULLIF(m.published_at, ''), lh.inferred_at) AS sort_date
            FROM location_history lh
            LEFT JOIN media_items m ON m.id = lh.media_id
            WHERE {where_sql}
            ORDER BY sort_date ASC, lh.is_manual_override DESC, lh.confidence DESC
            """,
            tuple(params),
        ).fetchall()

    # Si existe un override manual para un media_id, prevalece sobre la inferida
    by_media: dict = {}
    no_media = []
    for r in rows:
        d = dict(r)
        mid = d.get("media_id")
        if mid:
            prev = by_media.get(mid)
            if not prev or (d.get("is_manual_override") and not prev.get("is_manual_override")):
                by_media[mid] = d
        else:
            no_media.append(d)
    merged = list(by_media.values()) + no_media
    merged.sort(key=lambda x: (x.get("sort_date") or "", -int(x.get("is_manual_override") or 0)))
    return merged


# Para qué sirve: Registra una cuenta amiga aislada por su URL pública y la vincula opcionalmente al grafo SNA.
# Cómo funciona: Extrae el handle de la URL, inserta o actualiza `interactants` con `owner_type='friend'` y llama a `link_friend_to_subject_sna`.
def add_friend_by_url(
    case_id: str,
    url: str,
    platform: str = "instagram",
    label: str = "",
    status: str = "active",
    add_to_sna: bool = True,
) -> Optional[dict]:
    """Registra una cuenta amiga aislada (`owner_type='friend'`) por URL y la enlaza al SNA si procede."""
    if not case_exists(case_id):
        return None
    clean_url = (url or "").strip()
    plat = (platform or "instagram").strip().lower()
    # Extraer handle desde la URL pública
    tail = clean_url.split("?", 1)[0].split("#", 1)[0].rstrip("/").split("/")[-1]
    handle = re.sub(r"[^a-z0-9_.-]+", "_", tail.lstrip("@").lower()).strip("_") or f"amigo_{uuid.uuid4().hex[:6]}"
    now = _now_iso()
    with _connect() as conn:
        ex = conn.execute(
            "SELECT * FROM interactants WHERE case_id = ? AND platform = ? AND handle = ?",
            (case_id, plat, handle),
        ).fetchone()
        if ex:
            conn.execute(
                """
                UPDATE interactants
                SET owner_type = 'friend', profile_url = ?, label = ?, status = ?, last_seen = ?
                WHERE id = ?
                """,
                (clean_url or ex["profile_url"], label or ex["label"] or handle, status, now, ex["id"]),
            )
            row = conn.execute("SELECT * FROM interactants WHERE id = ?", (ex["id"],)).fetchone()
        else:
            cur = conn.execute(
                """
                INSERT INTO interactants (
                    case_id, platform, handle, display_name, profile_url,
                    first_seen, last_seen, owner_type, status, label
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'friend', ?, ?)
                """,
                (case_id, plat, handle, label or handle, clean_url, now, now, status, label or handle),
            )
            row = conn.execute("SELECT * FROM interactants WHERE id = ?", (cur.lastrowid,)).fetchone()
    friend_dict = dict(row) if row else None
    if friend_dict and add_to_sna:
        link_friend_to_subject_sna(
            case_id=case_id,
            friend_id=friend_dict["id"],
            visual_score=0.0,
            matches_count=1,
            kind="friend_link",
        )
    return friend_dict


# Para qué sirve: Lista todas las cuentas amigas registradas en un caso junto con sus contadores de datos aislados.
# Cómo funciona: Consulta `interactants` con `owner_type='friend'` y cuenta posts, medios y ubicaciones por `owner_id`.
def list_case_friends(case_id: str) -> list:
    """Lista las cuentas amigas registradas manualmente junto con sus contadores aislados."""
    if not case_exists(case_id):
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM interactants WHERE case_id = ? AND owner_type = 'friend' ORDER BY id DESC",
            (case_id,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            fid = d["id"]
            d["posts_count"] = int(conn.execute("SELECT COUNT(*) AS n FROM posts WHERE case_id = ? AND owner_type = 'friend' AND owner_id = ?", (case_id, fid)).fetchone()["n"])
            d["media_count"] = int(conn.execute("SELECT COUNT(*) AS n FROM media_items WHERE case_id = ? AND owner_type = 'friend' AND owner_id = ?", (case_id, fid)).fetchone()["n"])
            d["locations_count"] = int(conn.execute("SELECT COUNT(*) AS n FROM location_history WHERE case_id = ? AND owner_type = 'friend' AND owner_id = ?", (case_id, fid)).fetchone()["n"])
            out.append(d)
    return out


# Para qué sirve: Obtiene la ficha aislada de un amigo con sus posts, fotos, historial geográfico y resumen público.
# Cómo funciona: Filtra estrictamente por `owner_type='friend'` y `owner_id=friend_id`, extrae hashtags y lugares sin mezclar con el sujeto.
def get_friend_isolated_detail(case_id: str, friend_id: int) -> Optional[dict]:
    """Devuelve perfil, posts, fotos, historial de ubicaciones y resumen aislado del amigo."""
    if not case_exists(case_id):
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM interactants WHERE case_id = ? AND id = ? AND owner_type = 'friend'",
            (case_id, friend_id),
        ).fetchone()
        if not row:
            return None
        friend = dict(row)
        posts = [dict(r) for r in conn.execute(
            "SELECT * FROM posts WHERE case_id = ? AND owner_type = 'friend' AND owner_id = ? ORDER BY published_at DESC, id DESC",
            (case_id, friend_id),
        ).fetchall()]
        media_rows = conn.execute(
            "SELECT * FROM media_items WHERE case_id = ? AND owner_type = 'friend' AND owner_id = ? ORDER BY created_at DESC, id DESC",
            (case_id, friend_id),
        ).fetchall()
        media_items = []
        for mr in media_rows:
            md = dict(mr)
            md["metadata"] = _loads(md.pop("metadata_json", "{}"), {})
            md["vision"] = _loads(md.pop("vision_json", "{}"), {})
            media_items.append(md)
    loc_hist = list_location_history(case_id, owner_type="friend", owner_id=friend_id)
    # Resumen público aislado del amigo (bio, intereses/hashtags y lugares propios)
    interests = []
    seen_int = set()
    for p in posts:
        txt = p.get("content") or ""
        for tag in re.findall(r"#([A-Za-z0-9_\u00C0-\u024F]{2,40})", txt):
            tl = tag.lower()
            if tl not in seen_int:
                seen_int.add(tl)
                interests.append(f"#{tl}")
    places = []
    seen_pl = set()
    for lh in loc_hist:
        pl = ", ".join(x for x in (lh.get("city"), lh.get("region"), lh.get("country")) if x)
        if pl and pl.lower() not in seen_pl:
            seen_pl.add(pl.lower())
            places.append(pl)
    for p in posts:
        pl = (p.get("published_location") or "").strip()
        if pl and pl.lower() not in seen_pl:
            seen_pl.add(pl.lower())
            places.append(pl)

    return {
        "friend": friend,
        "posts": posts,
        "media_items": media_items,
        "location_history": loc_hist,
        "public_summary": {
            "bio": friend.get("bio") or "",
            "interests": interests[:12],
            "places": places[:12],
        },
    }


# Para qué sirve: Actualiza el estado operativo de un amigo en la tabla `interactants`.
# Cómo funciona: Ejecuta `UPDATE interactants SET status = ?, last_seen = ?` validando `owner_type='friend'`.
def update_friend_status(case_id: str, friend_id: int, status: str) -> None:
    """Actualiza el estado de acceso de un amigo (`active`, `pending_manual`, etc.)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE interactants SET status = ?, last_seen = ? WHERE case_id = ? AND id = ? AND owner_type = 'friend'",
            (status, _now_iso(), case_id, friend_id),
        )


# Para qué sirve: Crea o refuerza una arista entre el amigo y el sujeto principal dentro del grafo SNA.
# Cómo funciona: Determina el handle del sujeto, inserta o actualiza `interactions`, recalcula el SNA y deja bitácora de auditoría.
def link_friend_to_subject_sna(
    case_id: str,
    friend_id: int,
    visual_score: float = 0.0,
    matches_count: int = 0,
    kind: str = "visual_cooccurrence",
) -> Optional[dict]:
    """Enlaza explícitamente al amigo con el grafo SNA del sujeto (soporta `friend_link` automático o `visual_cooccurrence`)."""
    detail = get_case_detail(case_id)
    if not detail:
        return None
    f_detail = get_friend_isolated_detail(case_id, friend_id)
    if not f_detail:
        return None
    friend = f_detail["friend"]
    f_handle = (friend.get("handle") or "").strip().lstrip("@").lower()
    plat = (friend.get("platform") or "open").strip().lower()
    if not f_handle:
        return None

    subj_handles = [
        (i.get("value") or "").strip().lstrip("@").lower()
        for i in (detail.get("identifiers") or [])
        if i.get("kind") == "username" and i.get("value")
    ]
    if not subj_handles:
        for p in detail.get("profiles") or []:
            if p.get("username") and p.get("manual_status") != "rejected":
                subj_handles.append(p["username"].strip().lstrip("@").lower())
                break
    subj_handle = subj_handles[0] if subj_handles else re.sub(r"[^a-z0-9_.-]+", "_", (detail["case"].get("subject_name") or "sujeto").lower()).strip("_") or "sujeto"

    if f_handle == subj_handle:
        return {"case_id": case_id, "friend_handle": f_handle, "subject_handle": subj_handle, "sna_stats": {}}

    now = _now_iso()
    kind_val = (kind or "visual_cooccurrence").strip().lower()
    with _connect() as conn:
        ex = conn.execute(
            "SELECT id, evidence_count FROM interactions WHERE case_id = ? AND source_handle = ? AND target_handle = ? AND kind = ?",
            (case_id, f_handle, subj_handle, kind_val),
        ).fetchone()
        if ex:
            conn.execute(
                "UPDATE interactions SET weight = ?, visual_proximity_score = MAX(COALESCE(visual_proximity_score, 0), ?), evidence_count = ?, updated_at = ? WHERE id = ?",
                (max(1.5, round(visual_score / 25.0, 2)), round(visual_score, 1), max(1, int(matches_count or ex["evidence_count"])), now, ex["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO interactions (
                    case_id, source_handle, target_handle, kind, weight,
                    evidence_count, metadata_json, updated_at, visual_proximity_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    f_handle,
                    subj_handle,
                    kind_val,
                    max(1.5, round(visual_score / 25.0, 2)),
                    max(1, int(matches_count or 1)),
                    json.dumps({"platform": plat, "friend_id": friend_id, "confirmed_opt_in": True}, ensure_ascii=False),
                    now,
                    round(visual_score, 1),
                ),
            )
    sna = compute_and_save_case_sna(case_id, record_run=False)
    record_case_run(
        case_id,
        kind="audit:friend_link_sna",
        target=f_handle,
        status="done",
        summary={"friend_id": friend_id, "visual_proximity_score": visual_score, "matches": matches_count},
    )
    return {"case_id": case_id, "friend_handle": f_handle, "subject_handle": subj_handle, "sna_stats": (sna or {}).get("stats")}


# Para qué sirve: Almacena un lote extraído de un amigo manteniendo aislamiento total bajo `owner_type='friend'`.
# Cómo funciona: Valida pertenencia del `friend_id`, actualiza bio si existe e inserta posts y medios ligados únicamente a ese `owner_id`.
def ingest_friend_bundle(case_id: str, friend_id: int, bundle: dict) -> Optional[dict]:
    """Ingiere contenido de un amigo de forma ESTRICTAMENTE aislada (`owner_type='friend', owner_id=friend_id`)."""
    if not case_exists(case_id) or not isinstance(bundle, dict):
        return None
    now = _now_iso()
    platform = (bundle.get("platform") or "instagram").strip().lower()
    posts_in, media_in = 0, 0
    with _connect() as conn:
        f_row = conn.execute(
            "SELECT * FROM interactants WHERE case_id = ? AND id = ? AND owner_type = 'friend'",
            (case_id, friend_id),
        ).fetchone()
        if not f_row:
            return None
        prof = bundle.get("profile") or {}
        if prof.get("bio") or prof.get("display_name"):
            conn.execute(
                "UPDATE interactants SET bio = ?, display_name = COALESCE(NULLIF(?, ''), display_name), last_seen = ? WHERE id = ?",
                ((prof.get("bio") or "")[:600], prof.get("display_name") or "", now, friend_id),
            )
        ext_to_id = {}
        for p in bundle.get("posts") or []:
            ext_id = str(p.get("external_id") or "")
            cur = conn.execute(
                """
                INSERT INTO posts (
                    case_id, external_id, platform, author, content, published_at,
                    url, metadata_json, captured_at, published_location, owner_type, owner_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'friend', ?)
                """,
                (
                    case_id,
                    ext_id,
                    platform,
                    p.get("author") or f_row["handle"],
                    p.get("content") or "",
                    p.get("published_at") or "",
                    p.get("url") or "",
                    json.dumps(p.get("metadata") or {}, ensure_ascii=False),
                    now,
                    p.get("published_location") or "",
                    friend_id,
                ),
            )
            if ext_id:
                ext_to_id[ext_id] = cur.lastrowid
            posts_in += 1

        for m in bundle.get("media_items") or []:
            pid = ext_to_id.get(str(m.get("post_external_id") or ""))
            conn.execute(
                """
                INSERT INTO media_items (
                    case_id, post_id, filename, media_type, published_at, source_url,
                    alt_text, dhash, ocr_text, metadata_json, created_at, owner_type, owner_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'friend', ?)
                """,
                (
                    case_id,
                    pid,
                    m.get("filename") or "friend_media.jpg",
                    m.get("media_type") or "image",
                    m.get("published_at") or "",
                    m.get("source_url") or "",
                    (m.get("alt_text") or "")[:400],
                    m.get("dhash") or "",
                    m.get("ocr_text") or "",
                    json.dumps(m.get("metadata") or {}, ensure_ascii=False),
                    now,
                    friend_id,
                ),
            )
            media_in += 1
    return {"friend_id": friend_id, "posts_inserted": posts_in, "media_inserted": media_in}


# Para qué sirve: Borra en cascada todo el contenido aislado asociado a un amigo y actualiza el grafo SNA.
# Cómo funciona: Elimina filas de `location_history`, `media_items`, `posts`, `comments`, `friend_scan_runs`, enlaces y el interactuante, y recalcula SNA.
def delete_friend_cascade(case_id: str, friend_id: int) -> bool:
    """Elimina en cascada únicamente el contenido aislado perteneciente a `friend_id`."""
    if not case_exists(case_id):
        return False
    friend_handle = ""
    with _connect() as conn:
        ex = conn.execute(
            "SELECT id, handle FROM interactants WHERE case_id = ? AND id = ? AND owner_type = 'friend'",
            (case_id, friend_id),
        ).fetchone()
        if not ex:
            return False
        friend_handle = (ex["handle"] or "").strip().lower()
        conn.execute("DELETE FROM location_history WHERE case_id = ? AND owner_type = 'friend' AND owner_id = ?", (case_id, friend_id))
        conn.execute("DELETE FROM media_items WHERE case_id = ? AND owner_type = 'friend' AND owner_id = ?", (case_id, friend_id))
        conn.execute("DELETE FROM posts WHERE case_id = ? AND owner_type = 'friend' AND owner_id = ?", (case_id, friend_id))
        conn.execute("DELETE FROM comments WHERE case_id = ? AND owner_type = 'friend' AND owner_id = ?", (case_id, friend_id))
        conn.execute("DELETE FROM friend_scan_runs WHERE case_id = ? AND interactant_id = ?", (case_id, friend_id))
        if friend_handle:
            conn.execute(
                "DELETE FROM interactions WHERE case_id = ? AND LOWER(source_handle) = ? AND kind IN ('friend_link', 'visual_cooccurrence')",
                (case_id, friend_handle),
            )
        conn.execute("DELETE FROM interactants WHERE case_id = ? AND id = ?", (case_id, friend_id))
    compute_and_save_case_sna(case_id, record_run=False)
    return True


# Para qué sirve: Regenera las entradas inferidas de `subject_profile` preservando las revisiones humanas existentes.
# Cómo funciona: Respalda `(category, value) -> human_review` no pendientes, limpia la tabla para el caso e inserta las entradas nuevas.
def replace_subject_profile_entries(case_id: str, entries: list) -> list:
    """Reemplaza las inferencias pendientes de `subject_profile` conservando las ya revisadas por humanos."""
    if not case_exists(case_id):
        return []
    now = _now_iso()
    with _connect() as conn:
        # Conservar decisiones previas de human_review por (category, value)
        prev_rows = conn.execute(
            "SELECT category, value, human_review FROM subject_profile WHERE case_id = ? AND human_review != 'pending'",
            (case_id,),
        ).fetchall()
        prev_reviews = {(r["category"], r["value"].lower()): r["human_review"] for r in prev_rows}
        conn.execute("DELETE FROM subject_profile WHERE case_id = ?", (case_id,))
        for e in entries or []:
            cat = str(e.get("category") or "hobbies")
            val = str(e.get("value") or "").strip()[:240]
            if not val:
                continue
            hr = prev_reviews.get((cat, val.lower()), e.get("human_review") or "pending")
            conn.execute(
                """
                INSERT INTO subject_profile (
                    case_id, category, value, confidence, evidence_refs,
                    epistemic_status, human_review, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    cat,
                    val,
                    round(max(0.0, min(1.0, float(e.get("confidence") or 0.5))), 3),
                    json.dumps(e.get("evidence_refs") or [], ensure_ascii=False),
                    e.get("epistemic_status") or "ambiguous",
                    hr,
                    now,
                ),
            )
    return list_subject_profile(case_id)


# Para qué sirve: Lista todas las entradas del perfil enriquecido del sujeto ordenadas por confianza descendente.
# Cómo funciona: Consulta `subject_profile` por `case_id` y decodifica el campo JSON `evidence_refs`.
def list_subject_profile(case_id: str) -> list:
    """Lista las entradas del perfil enriquecido del sujeto ordenadas por categoría y confianza."""
    if not case_exists(case_id):
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM subject_profile WHERE case_id = ? ORDER BY confidence DESC, id ASC",
            (case_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["evidence_refs"] = _loads(d.pop("evidence_refs", "[]"), [])
        out.append(d)
    return out


# Para qué sirve: Actualiza la revisión humana de una entrada específica del perfil enriquecido del sujeto.
# Cómo funciona: Valida que `human_review` esté permitido, actualiza la fila en `subject_profile` y la retorna decodificada.
def update_subject_profile_review(case_id: str, entry_id: int, human_review: str) -> Optional[dict]:
    """Actualiza el estado de revisión humana (`pending|validated|ambiguous|discarded`) de una entrada."""
    if human_review not in ("pending", "validated", "ambiguous", "discarded"):
        raise ValueError("Estado de revisión inválido.")
    now = _now_iso()
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE subject_profile SET human_review = ?, updated_at = ? WHERE case_id = ? AND id = ?",
            (human_review, now, case_id, entry_id),
        )
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM subject_profile WHERE id = ?", (entry_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["evidence_refs"] = _loads(d.pop("evidence_refs", "[]"), [])
    return d
