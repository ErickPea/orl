# -*- coding: utf-8 -*-
"""
Conector oficial de API pública para Bluesky (ATProto).
Usa exclusivamente `https://public.api.bsky.app/xrpc/` (sin login ni API key).
"""
import requests

from modules.connectors.common import (
    USER_AGENT,
    add_interaction,
    empty_bundle,
    extract_hashtags,
    extract_mentions,
    extract_urls,
)

BSKY_PUBLIC_API = "https://public.api.bsky.app/xrpc"


# Para qué sirve: normaliza un handle o URL de Bluesky al formato `usuario.bsky.social` (o DID).
# Cómo funciona: recorta `bsky.app/profile/` si es URL y añade `.bsky.social` si no tiene punto ni `did:`.
def _normalize_handle(handle: str) -> str:
    h = (handle or "").strip().lstrip("@").lower()
    if h.startswith("https://bsky.app/profile/"):
        h = h.split("/profile/", 1)[1].split("/", 1)[0]
    if h and "." not in h and not h.startswith("did:"):
        h = f"{h}.bsky.social"
    return h


# Para qué sirve: convierte un URI interno ATProto (`at://...`) en enlace web navegable de Bluesky.
# Cómo funciona: toma el último segmento (`rkey`) del URI y forma `https://bsky.app/profile/{handle}/post/{rkey}`.
def _at_uri_to_web_url(handle: str, uri: str) -> str:
    # at://did:plc:.../app.bsky.feed.post/<rkey>
    rkey = (uri or "").rstrip("/").split("/")[-1]
    if handle and rkey:
        return f"https://bsky.app/profile/{handle}/post/{rkey}"
    return ""


# Para qué sirve: extrae perfil público, posts, imágenes con alt-text, hilos de respuestas y likes en Bluesky.
# Cómo funciona: consulta `app.bsky.actor.getProfile`, `getAuthorFeed`, `getPostThread` y `getLikes` en el API público XRPC.
def fetch_bluesky(
    handle: str,
    limit: int = 25,
    include_replies: bool = True,
    include_reactions: bool = False,
    timeout: int = 12,
) -> dict:
    """Obtiene perfil público, publicaciones, fotos embebidas, respuestas y likes públicos en Bluesky."""
    actor = _normalize_handle(handle)
    bundle = empty_bundle("bluesky", actor)
    if not actor:
        bundle["warnings"].append("Handle de Bluesky vacío.")
        return bundle

    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    # 1) Perfil público
    try:
        r_prof = requests.get(
            f"{BSKY_PUBLIC_API}/app.bsky.actor.getProfile",
            params={"actor": actor},
            headers=headers,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        bundle["warnings"].append(f"Error de red consultando Bluesky: {exc}")
        return bundle

    if r_prof.status_code != 200:
        bundle["warnings"].append(f"Perfil de Bluesky no disponible (HTTP {r_prof.status_code}).")
        return bundle

    prof = r_prof.json() or {}
    resolved_handle = (prof.get("handle") or actor).lower()
    bundle["handle"] = resolved_handle
    bundle["profile_links"] = extract_urls(prof.get("description") or "")
    bundle["profile"] = {
        "site": "Bluesky",
        "url": f"https://bsky.app/profile/{resolved_handle}",
        "username": resolved_handle,
        "display_name": prof.get("displayName") or resolved_handle,
        "bio": prof.get("description") or "",
        "location": "",
        "avatar_url": prof.get("avatar") or "",
        "website": "",
        "sources": "Bluesky-API",
        "osint_confidence": "ALTA",
        "metadata": {
            "did": prof.get("did") or "",
            "followers_count": prof.get("followersCount"),
            "follows_count": prof.get("followsCount"),
            "posts_count": prof.get("postsCount"),
            "banner": prof.get("banner") or "",
        },
    }

    # 2) Publicaciones del autor
    try:
        r_feed = requests.get(
            f"{BSKY_PUBLIC_API}/app.bsky.feed.getAuthorFeed",
            params={"actor": resolved_handle, "limit": max(1, min(int(limit), 50))},
            headers=headers,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        bundle["warnings"].append(f"No se pudo leer el feed público de Bluesky: {exc}")
        return bundle

    if r_feed.status_code != 200:
        bundle["warnings"].append(f"Feed de Bluesky devolvió HTTP {r_feed.status_code}.")
        return bundle

    feed = (r_feed.json() or {}).get("feed") or []
    posts_for_threads = []

    for item in feed:
        post = item.get("post") or {}
        record = post.get("record") or {}
        author = (post.get("author") or {}).get("handle") or resolved_handle
        uri = post.get("uri") or ""
        text = record.get("text") or ""
        created_at = record.get("createdAt") or post.get("indexedAt") or ""
        web_url = _at_uri_to_web_url(author, uri)

        mentions = extract_mentions(text)
        hashtags = extract_hashtags(text)

        # Imágenes embebidas (con alt-text nativo de Bluesky)
        media_urls = []
        embed = post.get("embed") or {}
        images = embed.get("images") or (embed.get("media") or {}).get("images") or []
        for idx, img in enumerate(images):
            full_url = img.get("fullsize") or img.get("thumb") or ""
            if full_url:
                media_urls.append(full_url)
                bundle["media_items"].append({
                    "filename": f"bsky_{uri.split('/')[-1]}_{idx + 1}.jpg",
                    "media_type": "image",
                    "source_url": full_url,
                    "alt_text": img.get("alt") or "",
                    "post_external_id": uri,
                    "published_at": created_at,
                    "metadata": {"platform": "bluesky", "thumb": img.get("thumb") or ""},
                })

        is_repost = bool(item.get("reason") and "repost" in str(item.get("reason", {}).get("$type", "")).lower())

        bundle["posts"].append({
            "external_id": uri,
            "platform": "bluesky",
            "author": author.lower(),
            "content": text,
            "published_at": created_at,
            "url": web_url,
            "media_urls": media_urls,
            "mentions": mentions,
            "hashtags": hashtags,
            "metadata": {
                "like_count": post.get("likeCount", 0),
                "repost_count": post.get("repostCount", 0),
                "reply_count": post.get("replyCount", 0),
                "is_repost": is_repost,
            },
        })

        for m in mentions:
            add_interaction(bundle["interactions"], author, m, "mention", weight=1.0, metadata={"platform": "bluesky"})

        # Si es una respuesta del sujeto hacia otro usuario
        reply_parent = (item.get("reply") or {}).get("parent") or {}
        parent_author = ((reply_parent.get("author") or {}).get("handle") or "").lower()
        if parent_author and parent_author != author.lower():
            add_interaction(bundle["interactions"], author, parent_author, "reply", weight=1.5, metadata={"platform": "bluesky"})

        if include_replies and not is_repost and (post.get("replyCount") or 0) > 0 and uri:
            posts_for_threads.append(uri)

    # 3) Respuestas públicas de otros usuarios en los últimos 5 hilos con actividad
    for uri in posts_for_threads[:5]:
        try:
            r_th = requests.get(
                f"{BSKY_PUBLIC_API}/app.bsky.feed.getPostThread",
                params={"uri": uri, "depth": 1},
                headers=headers,
                timeout=timeout,
            )
            if r_th.status_code != 200:
                continue
            replies = ((r_th.json() or {}).get("thread") or {}).get("replies") or []
            for rep in replies[:15]:
                rpost = rep.get("post") or {}
                rrec = rpost.get("record") or {}
                rauthor = ((rpost.get("author") or {}).get("handle") or "").lower()
                ruri = rpost.get("uri") or ""
                rtext = rrec.get("text") or ""
                if not rauthor or not rtext:
                    continue
                bundle["comments"].append({
                    "external_id": ruri,
                    "post_external_id": uri,
                    "author_handle": rauthor,
                    "target_handle": resolved_handle,
                    "content": rtext,
                    "published_at": rrec.get("createdAt") or "",
                    "metadata": {"platform": "bluesky", "url": _at_uri_to_web_url(rauthor, ruri)},
                })
                add_interaction(bundle["interactions"], rauthor, resolved_handle, "comment", weight=1.5, metadata={"platform": "bluesky"})
        except Exception:  # noqa: BLE001
            continue

    # 4) Reacciones públicas (likes) en los últimos 5 posts propios (opcional)
    if include_reactions:
        own_uris = [p["external_id"] for p in bundle["posts"] if p.get("external_id") and not (p.get("metadata") or {}).get("is_repost")][:5]
        for uri in own_uris:
            try:
                r_lk = requests.get(
                    f"{BSKY_PUBLIC_API}/app.bsky.feed.getLikes",
                    params={"uri": uri, "limit": 20},
                    headers=headers,
                    timeout=timeout,
                )
                if r_lk.status_code != 200:
                    continue
                for lk in (r_lk.json() or {}).get("likes") or []:
                    actor_h = ((lk.get("actor") or {}).get("handle") or "").lower()
                    if actor_h and actor_h != resolved_handle:
                        bundle["reactions"].append({
                            "post_external_id": uri,
                            "platform": "bluesky",
                            "reaction_type": "like",
                            "actor_handle": actor_h,
                            "count": 1,
                        })
                        add_interaction(bundle["interactions"], actor_h, resolved_handle, "like", weight=0.6, metadata={"platform": "bluesky"})
            except Exception:  # noqa: BLE001
                continue

    return bundle
