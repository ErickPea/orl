# -*- coding: utf-8 -*-
"""
Importador estructurado de contenido autorizado (JSON, CSV, ZIP de Meta y TXT de WhatsApp).

Soporta:
  1. Plantilla JSON/CSV de OSINT-Auto (listas o diccionarios con `posts`/`comments`/`profile`).
  2. Exportaciones oficiales de Meta (Facebook / Instagram / Threads) en `.json` o `.zip`:
       - Comentarios (`comments_v2`, `post_comments`, `string_map_data`)
       - Amigos / seguidores / seguidos (`friends_v2`, `relationships_following`, `relationships_followers`)
       - Reacciones / likes (`likes_media_likes`, `reactions_v2`)
       - Publicaciones (`data[].post`, `title`, `timestamp`)
       - Corrección automática de codificación mojibake típica de los JSON de Meta.
  3. Chats exportados de WhatsApp (`.txt`, formatos Android e iOS).
"""
import csv
import io
import json
import re
import zipfile
from datetime import datetime, timezone
from typing import Optional

from modules.connectors.common import (
    add_interaction,
    empty_bundle,
    extract_hashtags,
    extract_mentions,
)

# Patrones estándar de exportación de chats de WhatsApp (Android e iOS)
_WA_LINE_RE = re.compile(
    r"^\[?(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4})[,\s]+(\d{1,2}:\d{2}(?::\d{2})?(?:\s*[ap]\.?\s*m\.?)?)\]?\s*(?:-|—)?\s*([^:]{1,60}):\s+(.*)$",
    re.I,
)


# Para qué sirve: entrega una plantilla JSON de ejemplo compatible con el importador de OSINT-Auto.
# Cómo funciona: retorna un diccionario estático con estructura `profile`, `posts` y `comments`.
def sample_import_template() -> dict:
    """Plantilla JSON de ejemplo lista para descargar o adaptar."""
    return {
        "platform": "exportacion_propia",
        "subject_handle": "mi_usuario",
        "profile": {
            "site": "MiRed",
            "url": "https://ejemplo.com/mi_usuario",
            "username": "mi_usuario",
            "display_name": "Nombre Visible",
            "bio": "Biografía pública de ejemplo #tecnologia #fotografia",
            "location": "Bogotá, Colombia",
            "avatar_url": "",
            "website": "",
        },
        "posts": [
            {
                "id": "post_001",
                "author": "mi_usuario",
                "content": "Disfrutando el fin de semana en la montaña con @amigo_cercano #senderismo #fotografia",
                "published_at": "2026-10-15 09:30:00",
                "url": "https://ejemplo.com/mi_usuario/post_001",
                "media_urls": ["https://ejemplo.com/fotos/montana.jpg"],
            }
        ],
        "comments": [
            {
                "id": "com_001",
                "post_id": "post_001",
                "author": "amigo_cercano",
                "target": "mi_usuario",
                "content": "¡Gran ruta! Repetimos el próximo mes #senderismo",
                "published_at": "2026-10-15 10:12:00",
            },
            {
                "id": "com_002",
                "post_id": "post_001",
                "author": "mi_usuario",
                "target": "amigo_cercano",
                "content": "¡De una @amigo_cercano! Llevo la cámara.",
                "published_at": "2026-10-15 10:25:00",
            },
        ],
    }


# Para qué sirve: repara cadenas corruptas (mojibake) presentes en exportaciones oficiales JSON de Meta.
# Cómo funciona: recodifica de `latin-1` a `utf-8` devolviendo el valor original si no aplica.
def _fix_meta_encoding(val: str) -> str:
    """Corrige el clásico doble-encoding Latin-1 -> UTF-8 de las exportaciones JSON de Meta."""
    if not isinstance(val, str) or not val:
        return ""
    try:
        return val.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return val


# Para qué sirve: convierte timestamps epoch (segundos o milisegundos) a fecha UTC legible.
# Cómo funciona: divide entre 1000 si supera `1e12` y formatea con `datetime.fromtimestamp(..., utc)`.
def _ts_to_iso(ts) -> str:
    if ts in (None, ""):
        return ""
    try:
        val = float(ts)
        if val > 1e12:
            val /= 1000.0
        return datetime.fromtimestamp(val, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)


# Para qué sirve: toma el primer valor no vacío entre varias claves alternativas de un diccionario importado.
# Cómo funciona: recorre `keys` en orden y retorna `default` si ninguna tiene valor.
def _pick(d: dict, *keys, default=""):
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


# Para qué sirve: normaliza un objeto de publicación importado (JSON o CSV) dentro del `ConnectorBundle`.
# Cómo funciona: extrae texto, media, menciones y hashtags usando `_pick` y agrega aristas de mención.
def _ingest_post_obj(bundle: dict, obj: dict, idx: int, default_platform: str, default_author: str) -> None:
    content = str(_pick(obj, "content", "text", "caption", "body", "title", "message", default="")).strip()
    author = str(_pick(obj, "author", "username", "user", "author_handle", default=default_author)).strip().lstrip("@").lower()
    pub = str(_pick(obj, "published_at", "created_at", "date", "timestamp", default="")).strip()
    url = str(_pick(obj, "url", "link", "permalink", default="")).strip()
    ext_id = str(_pick(obj, "external_id", "id", "post_id", default=f"imp_post_{idx}")).strip()
    platform = str(_pick(obj, "platform", "site", "network", default=default_platform)).strip().lower()

    raw_media = obj.get("media_urls") or obj.get("images") or []
    if isinstance(raw_media, str):
        raw_media = [x.strip() for x in raw_media.replace(";", ",").split(",") if x.strip()]
    single_img = str(_pick(obj, "media_url", "image_url", "image", "photo", default="")).strip()
    if single_img and single_img not in raw_media:
        raw_media = [single_img, *raw_media]

    mentions = extract_mentions(content)
    hashtags = extract_hashtags(content)

    if content or raw_media:
        bundle["posts"].append({
            "external_id": ext_id,
            "platform": platform,
            "author": author or default_author,
            "content": content,
            "published_at": pub,
            "url": url,
            "media_urls": raw_media,
            "mentions": mentions,
            "hashtags": hashtags,
            "metadata": {"source": "import"},
        })

    for m_idx, murl in enumerate(raw_media):
        bundle["media_items"].append({
            "filename": f"import_{ext_id}_{m_idx + 1}.jpg",
            "media_type": "image",
            "source_url": murl,
            "alt_text": str(_pick(obj, "alt_text", "alt", default=content[:180])),
            "post_external_id": ext_id,
            "published_at": pub,
            "metadata": {"platform": platform, "source": "import"},
        })

    for m in mentions:
        add_interaction(bundle["interactions"], author or default_author, m, "mention", weight=1.0, metadata={"platform": platform})


# Para qué sirve: normaliza un comentario o respuesta importada en el `ConnectorBundle` generando su arista SNA.
# Cómo funciona: resuelve autor/destino/fecha con `_pick`, guarda el comentario y añade aristas `comment` y `mention`.
def _ingest_comment_obj(bundle: dict, obj: dict, idx: int, default_platform: str, default_subject: str) -> None:
    content = str(_pick(obj, "content", "text", "body", "comment", "message", default="")).strip()
    author = str(_pick(obj, "author", "author_handle", "username", "user", "from", default="")).strip().lstrip("@").lower()
    target = str(_pick(obj, "target", "target_handle", "reply_to", "to", default=default_subject)).strip().lstrip("@").lower()
    pub = str(_pick(obj, "published_at", "created_at", "date", "timestamp", default="")).strip()
    ext_id = str(_pick(obj, "external_id", "id", "comment_id", default=f"imp_com_{idx}")).strip()
    post_id = str(_pick(obj, "post_external_id", "post_id", "thread_id", default="")).strip()
    platform = str(_pick(obj, "platform", "site", "network", default=default_platform)).strip().lower()

    if not content and not author:
        return

    bundle["comments"].append({
        "external_id": ext_id,
        "post_external_id": post_id,
        "author_handle": author or "desconocido",
        "target_handle": target or default_subject,
        "content": content,
        "published_at": pub,
        "metadata": {"platform": platform, "source": "import"},
    })

    if author and target:
        add_interaction(bundle["interactions"], author, target, "comment", weight=1.5, metadata={"platform": platform})
    for m in extract_mentions(content):
        add_interaction(bundle["interactions"], author or default_subject, m, "mention", weight=1.0, metadata={"platform": platform})


# ---------------------------------------------------------------------------
# Parser nativo de exportaciones oficiales de Meta (Facebook / Instagram / Threads)
# ---------------------------------------------------------------------------
# Para qué sirve: detecta e ingiere archivos JSON oficiales de Facebook, Instagram o Threads sin configuración manual.
# Cómo funciona: procesa listas Meta (`string_map_data`, `string_list_data`, `friends_v2`, `comments_v2`, `data`) corrigiendo mojibake.
def _parse_meta_json_object(bundle: dict, data, platform: str, subject: str, source_tag: str = "meta") -> bool:
    """
    Detecta e ingiere estructuras estándar de exportación JSON de Facebook / Instagram / Threads.
    Devuelve True si reconoció y procesó estructuras Meta.
    """
    handled = False
    subj = (subject or "sujeto").strip().lstrip("@").lower()

    # Para qué sirve: recorre un arreglo de registros Meta diferenciando comentarios, seguidores, amigos y posts.
    # Cómo funciona: inspecciona la estructura (`string_map_data`, `string_list_data`, `name`, `data`) e inserta en `bundle`.
    def _handle_meta_items(items: list, hint_key: str = ""):
        nonlocal handled
        hk = hint_key.lower()
        for idx, it in enumerate(items, start=1):
            if not isinstance(it, dict):
                continue

            # Caso 1: Instagram comments / threads (`string_map_data`)
            smap = it.get("string_map_data")
            if isinstance(smap, dict):
                comment_txt = _fix_meta_encoding(
                    (smap.get("Comment") or smap.get("Comentario") or smap.get("Text") or {}).get("value", "")
                )
                owner = _fix_meta_encoding(
                    (smap.get("Media Owner") or smap.get("Propietario") or smap.get("Author") or {}).get("value", "")
                ).strip().lstrip("@").lower()
                ts = (smap.get("Time") or smap.get("Hora") or smap.get("Date") or {}).get("timestamp")
                pub = _ts_to_iso(ts)
                if comment_txt:
                    handled = True
                    target = owner or subj
                    author = subj if owner else "contacto_meta"
                    bundle["comments"].append({
                        "external_id": f"{source_tag}_smap_{idx}_{ts or idx}",
                        "post_external_id": "",
                        "author_handle": author,
                        "target_handle": target,
                        "content": comment_txt,
                        "published_at": pub,
                        "metadata": {"platform": platform, "source": "meta_export"},
                    })
                    if owner and owner != subj:
                        add_interaction(bundle["interactions"], subj, owner, "comment", weight=1.6, metadata={"platform": platform})
                    for m in extract_mentions(comment_txt):
                        add_interaction(bundle["interactions"], author, m, "mention", weight=1.0, metadata={"platform": platform})
                    continue

            # Caso 2: Instagram followers / following / likes (`string_list_data`)
            slist = it.get("string_list_data")
            if isinstance(slist, list) and slist:
                title_user = _fix_meta_encoding(it.get("title") or "").strip().lstrip("@").lower()
                for s_idx, entry in enumerate(slist, start=1):
                    if not isinstance(entry, dict):
                        continue
                    val = _fix_meta_encoding(entry.get("value") or "").strip().lstrip("@").lower()
                    peer = title_user or val
                    if not peer or peer in ("👍", "❤️", "like"):
                        continue
                    handled = True
                    if "follower" in hk:
                        add_interaction(bundle["interactions"], peer, subj, "follow", weight=1.1, metadata={"platform": platform})
                    elif "following" in hk:
                        add_interaction(bundle["interactions"], subj, peer, "follow", weight=1.2, metadata={"platform": platform})
                    elif "like" in hk or "react" in hk:
                        add_interaction(bundle["interactions"], subj, peer, "like", weight=1.0, metadata={"platform": platform})
                    else:
                        add_interaction(bundle["interactions"], subj, peer, "connection", weight=1.1, metadata={"platform": platform})
                continue

            # Caso 3: Facebook friends_v2 (`{"name": "...", "timestamp": ...}`)
            if "name" in it and ("friend" in hk or "timestamp" in it) and "data" not in it and "content" not in it:
                peer_name = _fix_meta_encoding(it.get("name") or "").strip()
                if peer_name:
                    handled = True
                    peer_slug = re.sub(r"\s+", "_", peer_name.lower())[:45]
                    add_interaction(bundle["interactions"], subj, peer_slug, "friend", weight=1.4, metadata={"platform": platform, "display_name": peer_name})
                    add_interaction(bundle["interactions"], peer_slug, subj, "friend", weight=1.4, metadata={"platform": platform, "display_name": peer_name})
                    continue

            # Caso 4: Facebook comments_v2 / posts (`{"timestamp": ..., "data": [{"comment": {...}} | {"post": "..."}], "title": "..."}`)
            fb_data = it.get("data")
            if isinstance(fb_data, list):
                pub = _ts_to_iso(it.get("timestamp"))
                title = _fix_meta_encoding(it.get("title") or "")
                for d_idx, sub in enumerate(fb_data, start=1):
                    if not isinstance(sub, dict):
                        continue
                    if isinstance(sub.get("comment"), dict):
                        cm = sub["comment"]
                        txt = _fix_meta_encoding(cm.get("comment") or "")
                        author = _fix_meta_encoding(cm.get("author") or subj).strip()
                        author_slug = re.sub(r"\s+", "_", author.lower())[:45] or subj
                        # Extraer a quién le comentó desde `title` si existe ("Erick comentó en la publicación de Ana.")
                        target_slug = subj
                        m_de = re.search(r"(?:de|of|on)\s+([A-ZÁÉÍÓÚÑ][^.'’\n]{2,45})", title)
                        if m_de:
                            target_slug = re.sub(r"\s+", "_", m_de.group(1).strip().lower())[:45]
                        if txt:
                            handled = True
                            bundle["comments"].append({
                                "external_id": f"{source_tag}_fbc_{idx}_{d_idx}",
                                "post_external_id": "",
                                "author_handle": author_slug,
                                "target_handle": target_slug,
                                "content": txt,
                                "published_at": _ts_to_iso(cm.get("timestamp")) or pub,
                                "metadata": {"platform": platform, "title": title},
                            })
                            if author_slug != target_slug:
                                add_interaction(bundle["interactions"], author_slug, target_slug, "comment", weight=1.6, metadata={"platform": platform})
                            for mn in extract_mentions(txt):
                                add_interaction(bundle["interactions"], author_slug, mn, "mention", weight=1.0, metadata={"platform": platform})
                    elif isinstance(sub.get("post"), str) and sub["post"].strip():
                        handled = True
                        txt = _fix_meta_encoding(sub["post"]).strip()
                        bundle["posts"].append({
                            "external_id": f"{source_tag}_fbp_{idx}_{d_idx}",
                            "platform": platform,
                            "author": subj,
                            "content": txt,
                            "published_at": pub,
                            "url": "",
                            "media_urls": [],
                            "mentions": extract_mentions(txt),
                            "hashtags": extract_hashtags(txt),
                            "metadata": {"source": "meta_export", "title": title},
                        })
                        for mn in extract_mentions(txt):
                            add_interaction(bundle["interactions"], subj, mn, "mention", weight=1.0, metadata={"platform": platform})

    if isinstance(data, dict):
        meta_keys = (
            "comments_v2", "comments_media_comments", "comments_reels_comments",
            "friends_v2", "relationships_following", "relationships_followers",
            "likes_media_likes", "reactions_v2", "status_updates_v2",
        )
        for k, v in data.items():
            if isinstance(v, list) and (k in meta_keys or any(h in k.lower() for h in ("comment", "friend", "follow", "like", "react"))):
                _handle_meta_items(v, hint_key=k)
    elif isinstance(data, list) and data and isinstance(data[0], dict):
        if any(k in data[0] for k in ("string_map_data", "string_list_data", "data")):
            _handle_meta_items(data, hint_key=source_tag)

    return handled


# ---------------------------------------------------------------------------
# Parser de chats exportados de WhatsApp (.txt)
# ---------------------------------------------------------------------------
# Para qué sirve: convierte un chat exportado de WhatsApp (`.txt`) en mensajes e interacciones de red.
# Cómo funciona: parsea líneas con `_WA_LINE_RE`, deduce al sujeto si falta (el participante más activo) y alterna emisor/receptor.
def _parse_whatsapp_txt(bundle: dict, text: str, subject: str, platform: str = "whatsapp") -> None:
    subj = (subject or "").strip().lstrip("@").lower()
    messages = []
    for raw_line in text.splitlines():
        line = raw_line.strip().lstrip("\u200e")
        m = _WA_LINE_RE.match(line)
        if not m:
            continue
        date_s, time_s, author_raw, body = m.groups()
        body_clean = body.strip()
        if not body_clean or "<multimedia omitido>" in body_clean.lower() or "media omitted" in body_clean.lower():
            continue
        author_slug = re.sub(r"\s+", "_", author_raw.strip().lower())[:45]
        messages.append((f"{date_s} {time_s}", author_slug, body_clean))

    if not messages:
        return

    # Si no se especificó sujeto, tomar el participante más frecuente o primer autor
    authors = {}
    for _, a, _ in messages:
        authors[a] = authors.get(a, 0) + 1
    if not subj and authors:
        subj = max(authors.items(), key=lambda x: x[1])[0]
        bundle["handle"] = subj

    other_authors = [a for a in authors if a != subj]
    default_peer = other_authors[0] if len(other_authors) == 1 else subj

    prev_author = ""
    for idx, (dt_s, author, body) in enumerate(messages, start=1):
        if author == subj:
            target = prev_author if (prev_author and prev_author != subj) else default_peer
        else:
            target = subj
        bundle["comments"].append({
            "external_id": f"wa_{idx}",
            "post_external_id": "",
            "author_handle": author,
            "target_handle": target,
            "content": body[:500],
            "published_at": dt_s,
            "metadata": {"platform": platform, "source": "whatsapp_txt"},
        })
        if author != target:
            add_interaction(bundle["interactions"], author, target, "comment", weight=1.5, metadata={"platform": platform})
        prev_author = author


# ---------------------------------------------------------------------------
# Punto de entrada principal del importador
# ---------------------------------------------------------------------------
# Para qué sirve: detecta el formato del archivo subido (`.zip`, `.txt`, `.csv`, `.json`) y lo convierte en un `ConnectorBundle`.
# Cómo funciona: despacha por extensión al parser de Meta ZIP, WhatsApp TXT, CSV o JSON (Meta/plantilla) tolerando errores de formato.
def parse_imported_content(
    raw_bytes: bytes,
    filename: str,
    platform_label: str = "import",
    subject_handle: Optional[str] = None,
) -> dict:
    """
    Parsea un archivo `.json`, `.csv`, `.zip` (exportación Meta) o `.txt` (chat WhatsApp)
    subido por el usuario y devuelve un `ConnectorBundle` normalizado.
    """
    ext = (filename or "").lower().rsplit(".", 1)[-1] if "." in (filename or "") else ""
    plat = (platform_label or "import").strip().lower() or "import"
    subj = (subject_handle or "").strip().lstrip("@").lower()
    bundle = empty_bundle(plat, subj or filename or "import")

    # 1) Archivo ZIP (ej. exportación oficial de Facebook / Instagram / Threads)
    if ext == "zip":
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                json_names = [n for n in zf.namelist() if n.lower().endswith(".json") and not n.startswith("__MACOSX")]
                processed = 0
                for name in json_names[:80]:
                    info = zf.getinfo(name)
                    if info.file_size > 10 * 1024 * 1024:
                        continue
                    try:
                        parsed = json.loads(zf.read(name).decode("utf-8-sig", errors="replace"))
                    except Exception:
                        continue
                    tag = name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
                    if _parse_meta_json_object(bundle, parsed, plat, subj, source_tag=tag):
                        processed += 1
                if processed == 0:
                    bundle["warnings"].append("El ZIP no contenía archivos JSON compatibles de Meta/Facebook/Instagram.")
        except zipfile.BadZipFile as exc:
            raise ValueError(f"El archivo ZIP está dañado o no es válido: {exc}") from exc
        return bundle

    text = raw_bytes.decode("utf-8-sig", errors="replace")

    # 2) Chat exportado de WhatsApp (.txt)
    if ext == "txt":
        _parse_whatsapp_txt(bundle, text, subj, platform=plat if plat != "exportacion_propia" else "whatsapp")
        if not bundle["comments"]:
            bundle["warnings"].append("No se detectaron mensajes con formato estándar de chat de WhatsApp en el archivo .txt.")
        return bundle

    # 3) Archivo CSV
    if ext == "csv":
        reader = csv.DictReader(io.StringIO(text))
        for idx, row in enumerate(reader, start=1):
            norm_row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k}
            row_type = (norm_row.get("type") or norm_row.get("kind") or "").lower()
            if row_type in ("comment", "reply", "comentario") or ("target" in norm_row and norm_row.get("target")):
                _ingest_comment_obj(bundle, norm_row, idx, plat, subj)
            else:
                _ingest_post_obj(bundle, norm_row, idx, plat, subj)
        return bundle

    # 4) Archivo JSON (Meta o plantilla OSINT-Auto)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"El archivo JSON no es válido: {exc}") from exc

    # Intentar primero formato oficial de exportación Meta (Facebook / Instagram / Threads)
    if _parse_meta_json_object(bundle, data, plat, subj, source_tag=(filename or "meta").rsplit(".", 1)[0]):
        return bundle

    if isinstance(data, dict):
        plat = str(data.get("platform") or plat).strip().lower()
        subj = str(data.get("subject_handle") or subj).strip().lstrip("@").lower()
        bundle["platform"] = plat
        bundle["handle"] = subj or bundle["handle"]

        if isinstance(data.get("profile"), dict) and data["profile"].get("url"):
            pr = data["profile"]
            bundle["profile"] = {
                "site": str(pr.get("site") or plat.title()),
                "url": str(pr.get("url")),
                "username": str(pr.get("username") or subj),
                "display_name": str(pr.get("display_name") or ""),
                "bio": str(pr.get("bio") or ""),
                "location": str(pr.get("location") or ""),
                "avatar_url": str(pr.get("avatar_url") or ""),
                "website": str(pr.get("website") or ""),
                "sources": "Importado",
                "osint_confidence": "ALTA",
                "metadata": {"source": "import"},
            }

        for idx, p in enumerate(data.get("posts") or [], start=1):
            if isinstance(p, dict):
                _ingest_post_obj(bundle, p, idx, plat, subj)
                for c_idx, c in enumerate(p.get("comments") or [], start=1):
                    if isinstance(c, dict):
                        c_copy = dict(c)
                        c_copy.setdefault("post_id", p.get("id") or f"imp_post_{idx}")
                        c_copy.setdefault("target", p.get("author") or subj)
                        _ingest_comment_obj(bundle, c_copy, idx * 1000 + c_idx, plat, subj)

        for idx, c in enumerate(data.get("comments") or [], start=1):
            if isinstance(c, dict):
                _ingest_comment_obj(bundle, c, idx, plat, subj)

        return bundle

    if isinstance(data, list):
        for idx, item in enumerate(data, start=1):
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type") or item.get("kind") or "").lower()
            if item_type in ("comment", "reply", "comentario") or item.get("target") or item.get("reply_to"):
                _ingest_comment_obj(bundle, item, idx, plat, subj)
            else:
                _ingest_post_obj(bundle, item, idx, plat, subj)
        return bundle

    raise ValueError("Formato JSON no reconocido: usa exportación Meta (JSON/ZIP), WhatsApp (.txt), CSV o plantilla OSINT-Auto.")
