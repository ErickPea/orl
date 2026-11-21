# -*- coding: utf-8 -*-
"""
Conector oficial de endpoints JSON públicos de Reddit (sin login ni API key).
Lee `/about.json`, `/submitted.json` y `/comments.json` para un usuario público.
"""
from datetime import datetime, timezone
from typing import Optional

import requests

from modules.connectors.common import (
    USER_AGENT,
    add_interaction,
    empty_bundle,
    extract_hashtags,
    extract_mentions,
    extract_urls,
)


def _ts_to_iso(ts) -> str:
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def _clean_username(handle: str) -> str:
    h = (handle or "").strip()
    if "reddit.com/" in h:
        parts = [p for p in h.split("/") if p]
        for idx, p in enumerate(parts):
            if p.lower() in ("u", "user") and idx + 1 < len(parts):
                return parts[idx + 1]
    return h.removeprefix("u/").removeprefix("/u/").lstrip("@").strip()


def fetch_reddit(
    handle: str,
    limit: int = 25,
    include_replies: bool = True,
    include_reactions: bool = False,
    fetch_post_replies: bool = False,
    access_token: Optional[str] = None,
    timeout: int = 12,
) -> dict:
    """Obtiene perfil público, envíos (posts), imágenes, comentarios y respuestas de terceros en Reddit."""
    user = _clean_username(handle)
    access_method = "oauth2_authorized" if access_token else "public_api"
    bundle = empty_bundle("reddit", user, access_method=access_method)
    if not user:
        bundle["warnings"].append("Username de Reddit vacío.")
        return bundle

    base_host = "https://oauth.reddit.com" if access_token else "https://www.reddit.com"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    # 1) Perfil (/about.json)
    try:
        r_about = requests.get(f"{base_host}/user/{user}/about.json", headers=headers, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        bundle["warnings"].append(f"Error de red consultando Reddit: {exc}")
        return bundle

    if r_about.status_code == 404:
        bundle["warnings"].append(f"Usuario u/{user} no encontrado en Reddit.")
        return bundle
    if r_about.status_code != 200:
        bundle["warnings"].append(f"Reddit devolvió HTTP {r_about.status_code} (posible rate-limit temporal).")
        return bundle

    data = (r_about.json() or {}).get("data") or {}
    sub = data.get("subreddit") or {}
    avatar = (data.get("icon_img") or sub.get("icon_img") or "").split("?")[0]
    bio_desc = sub.get("public_description") or ""
    bundle["profile_links"] = extract_urls(bio_desc)
    bundle["profile"] = {
        "site": "Reddit",
        "url": f"https://www.reddit.com/user/{user}",
        "username": data.get("name") or user,
        "display_name": sub.get("title") or data.get("name") or user,
        "bio": bio_desc,
        "location": "",
        "avatar_url": avatar,
        "website": "",
        "sources": "Reddit-OAuth2" if access_token else "Reddit-API",
        "osint_confidence": "ALTA",
        "metadata": {
            "total_karma": data.get("total_karma"),
            "link_karma": data.get("link_karma"),
            "comment_karma": data.get("comment_karma"),
            "created_utc": _ts_to_iso(data.get("created_utc")),
        },
    }

    lim = max(1, min(int(limit), 50))
    posts_for_replies = []

    # 2) Publicaciones enviadas (/submitted.json)
    try:
        r_sub = requests.get(
            f"{base_host}/user/{user}/submitted.json",
            params={"limit": lim},
            headers=headers,
            timeout=timeout,
        )
        if r_sub.status_code == 200:
            children = ((r_sub.json() or {}).get("data") or {}).get("children") or []
            for ch in children:
                p = ch.get("data") or {}
                pid = p.get("id") or ""
                title = p.get("title") or ""
                body = p.get("selftext") or ""
                content = f"{title}\n{body}".strip() if body else title
                sub_name = p.get("subreddit") or ""
                perm = f"https://www.reddit.com{p.get('permalink')}" if p.get("permalink") else ""
                created = _ts_to_iso(p.get("created_utc"))

                media_urls = []
                url_over = p.get("url_overridden_by_dest") or p.get("url") or ""
                if any(url_over.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif")):
                    media_urls.append(url_over)
                    bundle["media_items"].append({
                        "filename": f"reddit_{pid}.jpg",
                        "media_type": "image",
                        "source_url": url_over,
                        "alt_text": title[:200],
                        "post_external_id": pid,
                        "published_at": created,
                        "metadata": {"platform": "reddit", "subreddit": sub_name},
                    })

                mentions = extract_mentions(content)
                hashtags = extract_hashtags(content)
                if sub_name:
                    hashtags = list(dict.fromkeys([f"r/{sub_name.lower()}", *hashtags]))

                bundle["posts"].append({
                    "external_id": pid,
                    "platform": "reddit",
                    "author": user.lower(),
                    "content": content,
                    "published_at": created,
                    "url": perm or url_over,
                    "media_urls": media_urls,
                    "mentions": mentions,
                    "hashtags": hashtags,
                    "metadata": {
                        "subreddit": sub_name,
                        "score": p.get("score", 0),
                        "num_comments": p.get("num_comments", 0),
                    },
                })
                if include_reactions and (p.get("score") or 0) > 0:
                    bundle["reactions"].append({
                        "post_external_id": pid,
                        "platform": "reddit",
                        "reaction_type": "upvote",
                        "actor_handle": "",
                        "count": int(p["score"]),
                    })
                if fetch_post_replies and (p.get("num_comments") or 0) > 0 and p.get("permalink"):
                    posts_for_replies.append((pid, p["permalink"], sub_name))
                for m in mentions:
                    add_interaction(bundle["interactions"], user, m, "mention", weight=1.0, metadata={"platform": "reddit", "subreddit": sub_name})
    except Exception as exc:  # noqa: BLE001
        bundle["warnings"].append(f"No se pudieron leer envíos de Reddit: {exc}")

    # 3) Comentarios públicos hechos por el usuario (/comments.json)
    if include_replies:
        try:
            r_com = requests.get(
                f"{base_host}/user/{user}/comments.json",
                params={"limit": lim},
                headers=headers,
                timeout=timeout,
            )
            if r_com.status_code == 200:
                children = ((r_com.json() or {}).get("data") or {}).get("children") or []
                for ch in children:
                    c = ch.get("data") or {}
                    cid = c.get("id") or ""
                    body = c.get("body") or ""
                    sub_name = c.get("subreddit") or ""
                    link_author = (c.get("link_author") or "").lower()
                    created = _ts_to_iso(c.get("created_utc"))
                    c_url = f"https://www.reddit.com{c.get('permalink')}" if c.get("permalink") else ""
                    bundle["comments"].append({
                        "external_id": cid,
                        "post_external_id": c.get("link_id") or "",
                        "author_handle": user.lower(),
                        "target_handle": link_author if link_author and link_author != "[deleted]" else f"r/{sub_name.lower()}",
                        "content": body,
                        "published_at": created,
                        "metadata": {
                            "platform": "reddit",
                            "subreddit": sub_name,
                            "link_title": c.get("link_title") or "",
                            "score": c.get("score", 0),
                            "url": c_url,
                        },
                    })
                    if link_author and link_author not in ("[deleted]", "automoderator", user.lower()):
                        add_interaction(bundle["interactions"], user, link_author, "comment", weight=1.2, metadata={"platform": "reddit", "subreddit": sub_name})
                    for m in extract_mentions(body):
                        add_interaction(bundle["interactions"], user, m, "mention", weight=1.0, metadata={"platform": "reddit"})
        except Exception as exc:  # noqa: BLE001
            bundle["warnings"].append(f"No se pudieron leer comentarios de Reddit: {exc}")

    # 4) Respuestas de terceros dentro de los hilos propios del usuario (para medir reciprocidad real)
    if fetch_post_replies and posts_for_replies:
        for pid, permalink, sub_name in posts_for_replies[:5]:
            try:
                r_th = requests.get(f"{base_host}{permalink.rstrip('/')}.json", params={"limit": 15}, headers=headers, timeout=timeout)
                if r_th.status_code != 200:
                    continue
                listings = r_th.json()
                if not isinstance(listings, list) or len(listings) < 2:
                    continue
                comm_children = ((listings[1] or {}).get("data") or {}).get("children") or []
                for ch in comm_children[:15]:
                    cd = ch.get("data") or {}
                    c_auth = (cd.get("author") or "").lower()
                    c_body = cd.get("body") or ""
                    if not c_auth or c_auth in ("[deleted]", "automoderator", user.lower()) or not c_body:
                        continue
                    c_perm = f"https://www.reddit.com{cd.get('permalink')}" if cd.get("permalink") else f"https://www.reddit.com{permalink}"
                    bundle["comments"].append({
                        "external_id": f"th_{cd.get('id') or ''}",
                        "post_external_id": pid,
                        "author_handle": c_auth,
                        "target_handle": user.lower(),
                        "content": c_body[:1000],
                        "published_at": _ts_to_iso(cd.get("created_utc")),
                        "metadata": {"platform": "reddit", "subreddit": sub_name, "url": c_perm},
                    })
                    add_interaction(bundle["interactions"], c_auth, user, "comment", weight=1.5, metadata={"platform": "reddit", "subreddit": sub_name})
            except Exception:  # noqa: BLE001
                continue

    return bundle
