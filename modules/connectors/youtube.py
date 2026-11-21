# -*- coding: utf-8 -*-
"""
Conector oficial de YouTube Data API v3 (`https://www.googleapis.com/youtube/v3`).
Requiere `YOUTUBE_API_KEY` en `.env` (gratuita desde Google Cloud Console).
"""
import os

import requests

from modules.connectors.common import (
    USER_AGENT,
    add_interaction,
    empty_bundle,
    extract_hashtags,
    extract_mentions,
)

YT_API = "https://www.googleapis.com/youtube/v3"


# Para qué sirve: indica si la integración oficial con YouTube Data API v3 está habilitada.
# Cómo funciona: verifica que `YOUTUBE_API_KEY` tenga valor no vacío en el entorno.
def youtube_configured() -> bool:
    return bool(os.environ.get("YOUTUBE_API_KEY", "").strip())


# Para qué sirve: normaliza un handle `@canal`, ID `UC...` o URL de YouTube antes de consultar la API.
# Cómo funciona: extrae `@handle` o ID tras `/channel/` cuando recibe una URL completa de `youtube.com`.
def _clean_handle(handle: str) -> str:
    h = (handle or "").strip()
    if "youtube.com/" in h:
        part = h.split("youtube.com/", 1)[1].strip("/")
        if part.startswith("@"):
            return part.split("/", 1)[0]
        if part.startswith("channel/"):
            return part.split("/", 2)[1]
    return h


# Para qué sirve: consulta perfil de canal, videos recientes, miniaturas y comentarios públicos vía YouTube Data API v3.
# Cómo funciona: resuelve el canal en `/channels`, lista videos en `/search` y lee hilos en `/commentThreads` para los últimos 3 videos.
def fetch_youtube(handle: str, limit: int = 15, include_replies: bool = True, timeout: int = 12) -> dict:
    """Consulta canal público, videos recientes, miniaturas y comentarios públicos vía YouTube Data API v3."""
    clean = _clean_handle(handle)
    bundle = empty_bundle("youtube", clean)
    api_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not api_key:
        bundle["warnings"].append("YOUTUBE_API_KEY no está configurada en `.env`. Agrégala para consultar la API oficial de YouTube.")
        return bundle
    if not clean:
        bundle["warnings"].append("Handle o ID de canal de YouTube vacío.")
        return bundle

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    # 1) Buscar canal por forHandle (si empieza con @ o texto normal) o por ID (UC...)
    params = {"part": "snippet,statistics", "key": api_key}
    if clean.startswith("UC") and len(clean) >= 20:
        params["id"] = clean
    else:
        params["forHandle"] = clean if clean.startswith("@") else f"@{clean}"

    try:
        r_ch = requests.get(f"{YT_API}/channels", params=params, headers=headers, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        bundle["warnings"].append(f"Error consultando YouTube API: {exc}")
        return bundle

    if r_ch.status_code != 200:
        bundle["warnings"].append(f"YouTube API devolvió HTTP {r_ch.status_code}.")
        return bundle

    items = (r_ch.json() or {}).get("items") or []
    if not items:
        bundle["warnings"].append(f"Canal de YouTube no encontrado para '{clean}'.")
        return bundle

    ch = items[0]
    channel_id = ch.get("id") or ""
    snip = ch.get("snippet") or {}
    stats = ch.get("statistics") or {}
    custom_url = snip.get("customUrl") or clean
    avatar = ((snip.get("thumbnails") or {}).get("high") or (snip.get("thumbnails") or {}).get("default") or {}).get("url") or ""

    bundle["handle"] = custom_url
    bundle["profile"] = {
        "site": "YouTube",
        "url": f"https://www.youtube.com/{custom_url}" if custom_url.startswith("@") else f"https://www.youtube.com/channel/{channel_id}",
        "username": custom_url.lstrip("@"),
        "display_name": snip.get("title") or custom_url,
        "bio": snip.get("description") or "",
        "location": snip.get("country") or "",
        "avatar_url": avatar,
        "website": "",
        "sources": "YouTube-API",
        "osint_confidence": "ALTA",
        "metadata": {
            "channel_id": channel_id,
            "subscriber_count": stats.get("subscriberCount"),
            "video_count": stats.get("videoCount"),
            "view_count": stats.get("viewCount"),
        },
    }

    # 2) Videos públicos recientes del canal
    video_ids = []
    try:
        r_vid = requests.get(
            f"{YT_API}/search",
            params={
                "part": "snippet",
                "channelId": channel_id,
                "type": "video",
                "order": "date",
                "maxResults": max(1, min(int(limit), 25)),
                "key": api_key,
            },
            headers=headers,
            timeout=timeout,
        )
        if r_vid.status_code == 200:
            for item in (r_vid.json() or {}).get("items") or []:
                vid_id = (item.get("id") or {}).get("videoId") or ""
                vs = item.get("snippet") or {}
                title = vs.get("title") or ""
                desc = vs.get("description") or ""
                content = f"{title}\n{desc}".strip() if desc else title
                pub = vs.get("publishedAt") or ""
                thumb = ((vs.get("thumbnails") or {}).get("high") or (vs.get("thumbnails") or {}).get("default") or {}).get("url") or ""

                if thumb and vid_id:
                    bundle["media_items"].append({
                        "filename": f"yt_{vid_id}.jpg",
                        "media_type": "image",
                        "source_url": thumb,
                        "alt_text": title[:200],
                        "post_external_id": vid_id,
                        "published_at": pub,
                        "metadata": {"platform": "youtube", "video_id": vid_id},
                    })

                bundle["posts"].append({
                    "external_id": vid_id,
                    "platform": "youtube",
                    "author": custom_url.lstrip("@").lower(),
                    "content": content,
                    "published_at": pub,
                    "url": f"https://www.youtube.com/watch?v={vid_id}" if vid_id else "",
                    "media_urls": [thumb] if thumb else [],
                    "mentions": extract_mentions(content),
                    "hashtags": extract_hashtags(content),
                    "metadata": {"channel_id": channel_id},
                })
                if vid_id:
                    video_ids.append(vid_id)
    except Exception as exc:  # noqa: BLE001
        bundle["warnings"].append(f"No se pudieron listar videos del canal: {exc}")

    # 3) Comentarios públicos y respuestas anidadas en los últimos 3 videos
    if include_replies:
        channel_handle_l = custom_url.lstrip("@").lower()
        for vid_id in video_ids[:3]:
            try:
                r_c = requests.get(
                    f"{YT_API}/commentThreads",
                    params={"part": "snippet,replies", "videoId": vid_id, "maxResults": 10, "textFormat": "plainText", "key": api_key},
                    headers=headers,
                    timeout=timeout,
                )
                if r_c.status_code != 200:
                    continue
                for th in (r_c.json() or {}).get("items") or []:
                    top = ((th.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
                    cid = th.get("id") or ""
                    author = (top.get("authorDisplayName") or "").lstrip("@").lower()
                    text = top.get("textDisplay") or ""
                    if author and text:
                        bundle["comments"].append({
                            "external_id": cid,
                            "post_external_id": vid_id,
                            "author_handle": author,
                            "target_handle": channel_handle_l,
                            "content": text[:1000],
                            "published_at": top.get("publishedAt") or "",
                            "metadata": {"platform": "youtube", "like_count": top.get("likeCount", 0), "url": f"https://www.youtube.com/watch?v={vid_id}"},
                        })
                        add_interaction(bundle["interactions"], author, channel_handle_l, "comment", weight=1.2, metadata={"platform": "youtube"})
                    for rep in ((th.get("replies") or {}).get("comments") or [])[:10]:
                        rsnip = rep.get("snippet") or {}
                        r_auth = (rsnip.get("authorDisplayName") or "").lstrip("@").lower()
                        r_txt = rsnip.get("textDisplay") or ""
                        if r_auth and r_txt:
                            r_tgt = author if r_auth == channel_handle_l else channel_handle_l
                            bundle["comments"].append({
                                "external_id": str(rep.get("id") or ""),
                                "post_external_id": vid_id,
                                "author_handle": r_auth,
                                "target_handle": r_tgt,
                                "content": r_txt[:1000],
                                "published_at": rsnip.get("publishedAt") or "",
                                "metadata": {"platform": "youtube", "url": f"https://www.youtube.com/watch?v={vid_id}"},
                            })
                            add_interaction(bundle["interactions"], r_auth, r_tgt, "reply", weight=1.5, metadata={"platform": "youtube"})
            except Exception:  # noqa: BLE001
                continue

    return bundle
