# -*- coding: utf-8 -*-
"""Repositorio de ingesta normalizada y consulta de contenido del caso."""
import json
import re
from typing import Optional

from .db import _connect, _loads, _now_iso
from .cases_repo import (
    _get_primary_entity_id,
    add_identifier,
    case_exists,
    recalculate_case_resolution,
    record_case_run,
    upsert_profile,
)
from modules.entity_resolution import normalize_text

def _upsert_interactant_conn(
    conn,
    case_id: str,
    platform: str,
    handle: str = "",
    display_name: str = "",
    profile_url: str = "",
    seen_at: str = "",
) -> Optional[int]:
    """Inserta o actualiza un interactuante nominal y devuelve su `id`."""
    h = (handle or "").strip().lstrip("@").lower()
    dn = (display_name or "").strip()
    if not h and not dn:
        return None
    if not h and dn:
        h = re.sub(r"[^a-z0-9_.-]+", "_", dn.lower()).strip("_") or "desconocido"
    plat = (platform or "open").strip().lower()
    ts = (seen_at or "").strip() or _now_iso()
    ex = conn.execute(
        "SELECT id, display_name, profile_url, first_seen, last_seen FROM interactants WHERE case_id = ? AND platform = ? AND handle = ?",
        (case_id, plat, h),
    ).fetchone()
    if ex:
        new_dn = dn or ex["display_name"] or h
        new_url = (profile_url or "").strip() or ex["profile_url"] or ""
        first_s = ex["first_seen"] or ts
        if ts and first_s and ts < first_s:
            first_s = ts
        last_s = ex["last_seen"] or ts
        if ts and last_s and ts > last_s:
            last_s = ts
        conn.execute(
            "UPDATE interactants SET display_name = ?, profile_url = ?, first_seen = ?, last_seen = ? WHERE id = ?",
            (new_dn, new_url, first_s, last_s, ex["id"]),
        )
        return int(ex["id"])
    cur = conn.execute(
        """
        INSERT INTO interactants (case_id, platform, handle, display_name, profile_url, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (case_id, plat, h, dn or h, (profile_url or "").strip(), ts, ts),
    )
    return int(cur.lastrowid)


# ---------------------------------------------------------------------------
# Fase 2 :: Ingesta estructurada de conectores abiertos e importador
# ---------------------------------------------------------------------------
def ingest_connector_bundle(case_id: str, bundle: dict, job_id: str = "") -> Optional[dict]:
    """
    Persiste un `ConnectorBundle` normalizado (de API abierta o importador JSON/CSV)
    en SQLite deduplicando perfiles, publicaciones, media, comentarios e interacciones.
    """
    if not case_id or not case_exists(case_id) or not isinstance(bundle, dict):
        return None

    platform = (bundle.get("platform") or "open").strip().lower()
    handle = (bundle.get("handle") or "").strip()
    now = _now_iso()

    profile_id = None
    prof_obj = bundle.get("profile")
    if isinstance(prof_obj, dict) and prof_obj.get("url"):
        saved_prof = upsert_profile(
            case_id=case_id,
            site=prof_obj.get("site") or platform.title(),
            url=prof_obj["url"],
            username=prof_obj.get("username") or handle,
            display_name=prof_obj.get("display_name") or "",
            bio=prof_obj.get("bio") or "",
            location=prof_obj.get("location") or "",
            avatar_url=prof_obj.get("avatar_url") or "",
            website=prof_obj.get("website") or "",
            sources=prof_obj.get("sources") or f"{platform}-API",
            osint_confidence=prof_obj.get("osint_confidence") or "ALTA",
            metadata=prof_obj.get("metadata") or {},
            recalc=False,
        )
        if saved_prof:
            profile_id = saved_prof["id"]
        if prof_obj.get("username"):
            add_identifier(case_id, "username", prof_obj["username"], source=platform, confidence="ALTA", recalc=False)
        if prof_obj.get("location"):
            add_identifier(case_id, "location", prof_obj["location"], source=platform, confidence="MEDIA", recalc=False)

    posts_inserted = 0
    comments_inserted = 0
    media_inserted = 0
    reactions_inserted = 0
    interactions_upserted = 0
    evidences_inserted = 0
    ext_post_to_db_id = {}
    access_method = (bundle.get("access_method") or "public_api").strip()

    with _connect() as conn:
        entity_id = _get_primary_entity_id(conn, case_id)

        # 0) Snapshot histórico del perfil y evidencia de perfil
        if isinstance(prof_obj, dict) and prof_obj.get("url"):
            pub_links = list(bundle.get("profile_links") or [])
            if prof_obj.get("website") and prof_obj["website"] not in pub_links:
                pub_links.insert(0, prof_obj["website"])
            conn.execute(
                """
                INSERT INTO profile_snapshots (
                    case_id, profile_id, platform, source_url, access_method,
                    display_name, username, bio, public_links_json, public_info_json, captured_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    profile_id,
                    platform,
                    prof_obj["url"],
                    access_method,
                    prof_obj.get("display_name") or "",
                    prof_obj.get("username") or handle,
                    prof_obj.get("bio") or "",
                    json.dumps(pub_links, ensure_ascii=False),
                    json.dumps(prof_obj.get("metadata") or {}, ensure_ascii=False),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO evidence_records (
                    case_id, record_kind, ref_table, ref_id, platform,
                    source_url, access_method, excerpt, captured_at
                ) VALUES (?, 'profile', 'profiles', ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    profile_id,
                    platform,
                    prof_obj["url"],
                    access_method,
                    (prof_obj.get("bio") or prof_obj.get("display_name") or "")[:240],
                    now,
                ),
            )
            evidences_inserted += 1

        # 1) Publicaciones (deduplicadas por case_id + platform + external_id)
        for p in bundle.get("posts") or []:
            ext_id = str(p.get("external_id") or "").strip()
            p_plat = str(p.get("platform") or platform).strip().lower()
            pub_loc = str(p.get("published_location") or (p.get("metadata") or {}).get("published_location") or "").strip()[:160]
            meta = {
                **(p.get("metadata") or {}),
                "media_urls": p.get("media_urls") or [],
                "mentions": p.get("mentions") or [],
                "hashtags": p.get("hashtags") or [],
            }
            existing = None
            if ext_id:
                existing = conn.execute(
                    "SELECT id FROM posts WHERE case_id = ? AND platform = ? AND external_id = ?",
                    (case_id, p_plat, ext_id),
                ).fetchone()
            if existing:
                db_pid = existing["id"]
                conn.execute(
                    """
                    UPDATE posts SET content = ?, published_at = ?, url = ?, published_location = ?, metadata_json = ?, captured_at = ?
                    WHERE id = ?
                    """,
                    (
                        p.get("content") or "",
                        p.get("published_at") or "",
                        p.get("url") or "",
                        pub_loc,
                        json.dumps(meta, ensure_ascii=False),
                        now,
                        db_pid,
                    ),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO posts (
                        case_id, entity_id, profile_id, external_id, platform, author,
                        content, published_at, url, published_location, metadata_json, captured_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        case_id,
                        entity_id,
                        profile_id,
                        ext_id,
                        p_plat,
                        (p.get("author") or handle).lower(),
                        p.get("content") or "",
                        p.get("published_at") or "",
                        p.get("url") or "",
                        pub_loc,
                        json.dumps(meta, ensure_ascii=False),
                        now,
                    ),
                )
                db_pid = cur.lastrowid
                posts_inserted += 1
                conn.execute(
                    """
                    INSERT INTO evidence_records (
                        case_id, record_kind, ref_table, ref_id, platform,
                        source_url, access_method, excerpt, captured_at
                    ) VALUES (?, 'post', 'posts', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        case_id,
                        db_pid,
                        p_plat,
                        p.get("url") or "",
                        access_method,
                        (p.get("content") or "")[:240],
                        now,
                    ),
                )
                evidences_inserted += 1
            if ext_id:
                ext_post_to_db_id[ext_id] = db_pid

        # 2) Media items públicos (deduplicados por case_id + source_url)
        for m in bundle.get("media_items") or []:
            surl = (m.get("source_url") or "").strip()
            if surl:
                ex_m = conn.execute(
                    "SELECT id FROM media_items WHERE case_id = ? AND source_url = ?",
                    (case_id, surl),
                ).fetchone()
                if ex_m:
                    continue
            post_db_id = ext_post_to_db_id.get(str(m.get("post_external_id") or ""))
            conn.execute(
                """
                INSERT INTO media_items (
                    case_id, entity_id, profile_id, post_id, filename, media_type,
                    captured_date, published_at, source_url, alt_text, metadata_json, tags_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?)
                """,
                (
                    case_id,
                    entity_id,
                    profile_id,
                    post_db_id,
                    m.get("filename") or "media.jpg",
                    m.get("media_type") or "image",
                    m.get("published_at") or "",
                    m.get("published_at") or "",
                    surl,
                    (m.get("alt_text") or "")[:400],
                    json.dumps(m.get("metadata") or {}, ensure_ascii=False),
                    now,
                ),
            )
            media_inserted += 1

        # 2b) Interactuantes explícitos del bundle
        for it_person in bundle.get("interactants") or []:
            _upsert_interactant_conn(
                conn,
                case_id=case_id,
                platform=str(it_person.get("platform") or platform).lower(),
                handle=it_person.get("handle") or "",
                display_name=it_person.get("display_name") or "",
                profile_url=it_person.get("profile_url") or "",
                seen_at=it_person.get("last_seen") or it_person.get("first_seen") or now,
            )

        # 3) Comentarios (deduplicados por case_id + external_id + author_handle)
        for c in bundle.get("comments") or []:
            ext_id = str(c.get("external_id") or "").strip()
            c_meta = c.get("metadata") or {}
            author_dn = (c.get("author_display_name") or c_meta.get("author_display_name") or "").strip()
            author_url = (c.get("author_profile_url") or c_meta.get("author_profile_url") or "").strip()
            raw_author = (c.get("author_handle") or "").strip().lstrip("@").lower()
            if not raw_author and author_dn:
                raw_author = re.sub(r"[^a-z0-9_.-]+", "_", author_dn.lower()).strip("_") or "desconocido"
            author = raw_author or "desconocido"
            target = (c.get("target_handle") or "").strip().lower()
            post_db_id = ext_post_to_db_id.get(str(c.get("post_external_id") or ""))
            c_plat = str(c_meta.get("platform") or platform).lower()
            interactant_db_id = None
            if author != "desconocido" or author_dn:
                interactant_db_id = _upsert_interactant_conn(
                    conn,
                    case_id=case_id,
                    platform=c_plat,
                    handle=author,
                    display_name=author_dn or author,
                    profile_url=author_url,
                    seen_at=c.get("published_at") or now,
                )
            if ext_id:
                ex_c = conn.execute(
                    "SELECT id, interactant_id FROM comments WHERE case_id = ? AND external_id = ? AND author_handle = ?",
                    (case_id, ext_id, author),
                ).fetchone()
                if ex_c:
                    if interactant_db_id and not ex_c["interactant_id"]:
                        conn.execute("UPDATE comments SET interactant_id = ? WHERE id = ?", (interactant_db_id, ex_c["id"]))
                    continue
            cur_c = conn.execute(
                """
                INSERT INTO comments (
                    case_id, post_id, external_id, author_handle, target_handle,
                    content, published_at, metadata_json, captured_at, interactant_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    post_db_id,
                    ext_id,
                    author,
                    target,
                    c.get("content") or "",
                    c.get("published_at") or "",
                    json.dumps(c_meta, ensure_ascii=False),
                    now,
                    interactant_db_id,
                ),
            )
            c_db_id = cur_c.lastrowid
            comments_inserted += 1
            conn.execute(
                """
                INSERT INTO evidence_records (
                    case_id, record_kind, ref_table, ref_id, platform,
                    source_url, access_method, excerpt, captured_at
                ) VALUES (?, 'comment', 'comments', ?, ?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    c_db_id,
                    str(c_meta.get("platform") or platform).lower(),
                    str(c_meta.get("url") or ""),
                    access_method,
                    (c.get("content") or "")[:240],
                    now,
                ),
            )
            evidences_inserted += 1

        # 3b) Reacciones públicas o agregadas por publicación
        for rx in bundle.get("reactions") or []:
            post_db_id = ext_post_to_db_id.get(str(rx.get("post_external_id") or ""))
            rtype = (rx.get("reaction_type") or "like").strip().lower()
            actor = (rx.get("actor_handle") or "").strip().lstrip("@").lower()
            rcount = max(1, int(rx.get("count") or 1))
            rplat = (rx.get("platform") or platform).strip().lower()
            ex_r = conn.execute(
                """
                SELECT id FROM reactions
                WHERE case_id = ? AND COALESCE(post_id, -1) = COALESCE(?, -1)
                  AND platform = ? AND reaction_type = ? AND actor_handle = ?
                """,
                (case_id, post_db_id, rplat, rtype, actor),
            ).fetchone()
            if ex_r:
                conn.execute(
                    "UPDATE reactions SET count = ?, captured_at = ? WHERE id = ?",
                    (rcount, now, ex_r["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO reactions (
                        case_id, post_id, platform, reaction_type, actor_handle, count, captured_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (case_id, post_db_id, rplat, rtype, actor, rcount, now),
                )
                reactions_inserted += 1

        # 4) Interacciones agregadas por (source_handle, target_handle, kind) dentro del lote
        batch_agg = {}
        for ix in bundle.get("interactions") or []:
            src = (ix.get("source_handle") or "").strip().lower()
            tgt = (ix.get("target_handle") or "").strip().lower()
            kind = (ix.get("kind") or "mention").strip().lower()
            if not src or not tgt or src == tgt:
                continue
            key = (src, tgt, kind)
            cur_agg = batch_agg.get(key)
            w = float(ix.get("weight") or 1.0)
            ev = int(ix.get("evidence_count") or 1)
            if cur_agg:
                cur_agg["weight"] += w
                cur_agg["evidence_count"] += ev
            else:
                batch_agg[key] = {
                    "weight": w,
                    "evidence_count": ev,
                    "metadata": ix.get("metadata") or {"platform": platform},
                }

        for (src, tgt, kind), agg in batch_agg.items():
            ex_ix = conn.execute(
                """
                SELECT id, weight, evidence_count FROM interactions
                WHERE case_id = ? AND source_handle = ? AND target_handle = ? AND kind = ?
                """,
                (case_id, src, tgt, kind),
            ).fetchone()
            if ex_ix:
                if (agg.get("metadata") or {}).get("manual"):
                    new_ev = int(ex_ix["evidence_count"]) + agg["evidence_count"]
                    new_w = float(ex_ix["weight"]) + agg["weight"]
                else:
                    # Mantenemos el máximo entre lo guardado y el lote para evitar inflación en re-ingestas idénticas
                    new_ev = max(int(ex_ix["evidence_count"]), agg["evidence_count"])
                    new_w = max(float(ex_ix["weight"]), agg["weight"])
                conn.execute(
                    "UPDATE interactions SET weight = ?, evidence_count = ?, updated_at = ? WHERE id = ?",
                    (new_w, new_ev, now, ex_ix["id"]),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO interactions (
                        case_id, source_handle, target_handle, kind, weight,
                        evidence_count, metadata_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        case_id,
                        src,
                        tgt,
                        kind,
                        agg["weight"],
                        agg["evidence_count"],
                        json.dumps(agg["metadata"], ensure_ascii=False),
                        now,
                    ),
                )
            interactions_upserted += 1

        conn.execute("UPDATE cases SET updated_at = ? WHERE id = ?", (now, case_id))

    summary = {
        "platform": platform,
        "handle": handle,
        "profile_saved": bool(profile_id),
        "posts_inserted": posts_inserted,
        "comments_inserted": comments_inserted,
        "media_inserted": media_inserted,
        "reactions_inserted": reactions_inserted,
        "interactions_upserted": interactions_upserted,
        "evidences_inserted": evidences_inserted,
        "warnings": bundle.get("warnings") or [],
    }
    record_case_run(
        case_id=case_id,
        job_id=job_id,
        kind=f"ingest:{platform}",
        target=handle or platform,
        status="done",
        folder="",
        summary=summary,
    )
    resolution = recalculate_case_resolution(case_id)
    summary["resolution"] = resolution
    return summary


def get_case_content(
    case_id: str,
    limit_posts: int = 50,
    limit_comments: int = 100,
    limit_media: int = 60,
    include_friends: bool = False,
) -> Optional[dict]:
    """Devuelve publicaciones, comentarios, fotos/media e interacciones ingeridas de un caso (excluye contenido aislado de amigos salvo `include_friends=True`)."""
    if not case_exists(case_id):
        return None

    iso_filter = "" if include_friends else " AND COALESCE(owner_type, 'subject') != 'friend'"

    with _connect() as conn:
        post_rows = conn.execute(
            f"SELECT * FROM posts WHERE case_id = ?{iso_filter} ORDER BY published_at DESC, id DESC LIMIT ?",
            (case_id, limit_posts),
        ).fetchall()
        comment_rows = conn.execute(
            f"SELECT * FROM comments WHERE case_id = ?{iso_filter} ORDER BY published_at DESC, id DESC LIMIT ?",
            (case_id, limit_comments),
        ).fetchall()
        media_rows = conn.execute(
            f"SELECT * FROM media_items WHERE case_id = ?{iso_filter} ORDER BY created_at DESC, id DESC LIMIT ?",
            (case_id, limit_media),
        ).fetchall()
        inter_rows = conn.execute(
            """
            SELECT * FROM interactions
            WHERE case_id = ?
            ORDER BY weight DESC, evidence_count DESC
            LIMIT 100
            """,
            (case_id,),
        ).fetchall()

        total_posts = int(conn.execute(f"SELECT COUNT(*) AS n FROM posts WHERE case_id = ?{iso_filter}", (case_id,)).fetchone()["n"])
        total_comments = int(conn.execute(f"SELECT COUNT(*) AS n FROM comments WHERE case_id = ?{iso_filter}", (case_id,)).fetchone()["n"])
        total_media = int(conn.execute(f"SELECT COUNT(*) AS n FROM media_items WHERE case_id = ?{iso_filter}", (case_id,)).fetchone()["n"])
        total_interactions = int(conn.execute("SELECT COUNT(*) AS n FROM interactions WHERE case_id = ?", (case_id,)).fetchone()["n"])

    hashtag_counts = {}
    mention_counts = {}

    posts = []
    for r in post_rows:
        d = dict(r)
        meta = _loads(d.pop("metadata_json", "{}"), {})
        d["metadata"] = meta
        d["hashtags"] = meta.get("hashtags") or []
        d["mentions"] = meta.get("mentions") or []
        d["media_urls"] = meta.get("media_urls") or []
        for h in d["hashtags"]:
            hashtag_counts[h] = hashtag_counts.get(h, 0) + 1
        for m in d["mentions"]:
            mention_counts[m] = mention_counts.get(m, 0) + 1
        posts.append(d)

    comments = []
    for r in comment_rows:
        d = dict(r)
        d["metadata"] = _loads(d.pop("metadata_json", "{}"), {})
        comments.append(d)

    media_items = []
    for r in media_rows:
        d = dict(r)
        d["metadata"] = _loads(d.pop("metadata_json", "{}"), {})
        d["tags"] = _loads(d.pop("tags_json", "[]"), [])
        d["vision"] = _loads(d.pop("vision_json", "{}"), {})
        media_items.append(d)

    interactions = []
    for r in inter_rows:
        d = dict(r)
        d["metadata"] = _loads(d.pop("metadata_json", "{}"), {})
        interactions.append(d)

    top_hashtags = [{"tag": k, "count": v} for k, v in sorted(hashtag_counts.items(), key=lambda x: -x[1])[:20]]
    top_mentions = [{"handle": k, "count": v} for k, v in sorted(mention_counts.items(), key=lambda x: -x[1])[:20]]

    return {
        "counts": {
            "posts": total_posts,
            "comments": total_comments,
            "media": total_media,
            "interactions": total_interactions,
        },
        "posts": posts,
        "comments": comments,
        "media_items": media_items,
        "interactions": interactions,
        "top_hashtags": top_hashtags,
        "top_mentions": top_mentions,
    }



def get_all_referenced_folders() -> set:
    """Devuelve el conjunto de carpetas de `output/` referenciadas por algún caso activo en SQLite."""
    folders = set()
    with _connect() as conn:
        for row in conn.execute("SELECT folder FROM case_runs WHERE folder != ''"):
            folders.add(row["folder"])
        for row in conn.execute("SELECT folder FROM media_items WHERE folder != ''"):
            folders.add(row["folder"])
    return folders


