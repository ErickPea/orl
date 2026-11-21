# -*- coding: utf-8 -*-
"""
Conector oficial de Meta Graph API (Instagram Professional y Páginas de Facebook).

Capacidades reales soportadas de forma 100% autorizada vía OAuth2:
  1. `instagram_pro`:
       - Cuenta propia profesional (Creator/Business) vinculada a una Página de Facebook:
         perfil, bio, publicaciones, imágenes/videos, comentarios, respuestas y conteos de likes.
       - `Business Discovery` sobre otra cuenta pública Profesional (Business/Creator):
         perfil público, bio, conteos y publicaciones recientes (Meta NO entrega comentarios
         ni lista de seguidores de terceros por esta API; se advierte explícitamente).
  2. `facebook_page`:
       - Página de Facebook administrada por el usuario autenticado vía OAuth2:
         información pública de la página, publicaciones, comentarios, respuestas y resumen de reacciones.

Límite explícito: Meta Graph API NO permite consultar perfiles personales privados ni
cuentas personales de terceros; si no hay token OAuth2 autorizado devuelve un aviso claro.
"""
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

META_GRAPH_API = "https://graph.facebook.com/v21.0"


# Para qué sirve: localiza el ID y username de la cuenta Instagram Business/Creator vinculada al usuario OAuth2.
# Cómo funciona: consulta `/me/accounts` en Meta Graph API buscando el primer objeto `instagram_business_account`.
def _resolve_ig_business_account(access_token: str, timeout: int = 12) -> tuple:
    """Busca la cuenta de Instagram Business/Creator vinculada a las Páginas del usuario autorizado."""
    r = requests.get(
        f"{META_GRAPH_API}/me/accounts",
        params={"fields": "id,name,access_token,instagram_business_account{id,username}", "access_token": access_token},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    if r.status_code != 200:
        return None, None, f"No se pudieron consultar páginas vinculadas (HTTP {r.status_code})."
    pages = (r.json() or {}).get("data") or []
    for pg in pages:
        ig = pg.get("instagram_business_account")
        if ig and ig.get("id"):
            return ig["id"], (ig.get("username") or "").lower(), ""
    return None, None, "No se encontró ninguna cuenta profesional de Instagram vinculada a tus Páginas de Facebook."


# Para qué sirve: extrae perfil, publicaciones y comentarios autorizados de Instagram Profesional vía Meta Graph API.
# Cómo funciona: lee cuenta propia completa (`/{ig_id}/media` + `/comments`) o usa `business_discovery` en cuentas Pro de terceros.
def fetch_instagram_pro(
    handle: str,
    limit: int = 20,
    include_replies: bool = True,
    include_reactions: bool = True,
    access_token: Optional[str] = None,
    timeout: int = 12,
) -> dict:
    """Consulta perfil y publicaciones de Instagram vía Meta Graph API oficial (OAuth2)."""
    target_user = (handle or "").strip().lstrip("@").lower()
    if "instagram.com/" in target_user:
        target_user = [p for p in target_user.split("instagram.com/", 1)[1].split("/") if p][0]

    bundle = empty_bundle("instagram_pro", target_user, access_method="oauth2_authorized")
    if not access_token:
        bundle["warnings"].append(
            "Instagram requiere conexión OAuth2 autorizada de Meta Graph API (cuenta Profesional). "
            "Para cuentas personales, usa el importador oficial de exportación JSON/ZIP de Meta."
        )
        return bundle

    own_ig_id, own_ig_user, err = _resolve_ig_business_account(access_token, timeout=timeout)
    if not own_ig_id:
        bundle["warnings"].append(err)
        return bundle

    lim = max(1, min(int(limit), 30))

    # Caso A: La cuenta investigada es la propia cuenta Profesional autorizada (acceso completo a comentarios!)
    if not target_user or target_user == own_ig_user:
        r_prof = requests.get(
            f"{META_GRAPH_API}/{own_ig_id}",
            params={
                "fields": "id,username,name,biography,website,profile_picture_url,followers_count,follows_count,media_count",
                "access_token": access_token,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        if r_prof.status_code != 200:
            bundle["warnings"].append(f"Meta Graph API devolvió HTTP {r_prof.status_code} al leer perfil propio.")
            return bundle
        pr = r_prof.json() or {}
        uname = (pr.get("username") or own_ig_user).lower()
        bio = pr.get("biography") or ""
        bundle["handle"] = uname
        bundle["profile_links"] = extract_urls(f"{bio} {pr.get('website') or ''}")
        bundle["profile"] = {
            "site": "Instagram",
            "url": f"https://www.instagram.com/{uname}/",
            "username": uname,
            "display_name": pr.get("name") or uname,
            "bio": bio,
            "location": "",
            "avatar_url": pr.get("profile_picture_url") or "",
            "website": pr.get("website") or "",
            "sources": "Meta-Graph-OAuth2",
            "osint_confidence": "ALTA",
            "metadata": {
                "ig_id": own_ig_id,
                "followers_count": pr.get("followers_count"),
                "follows_count": pr.get("follows_count"),
                "media_count": pr.get("media_count"),
            },
        }

        r_med = requests.get(
            f"{META_GRAPH_API}/{own_ig_id}/media",
            params={
                "fields": "id,caption,media_type,media_url,thumbnail_url,permalink,timestamp,like_count,comments_count",
                "limit": lim,
                "access_token": access_token,
            },
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
        )
        if r_med.status_code != 200:
            bundle["warnings"].append(f"No se pudieron leer publicaciones de Instagram (HTTP {r_med.status_code}).")
            return bundle

        for m in (r_med.json() or {}).get("data") or []:
            mid = str(m.get("id") or "")
            caption = m.get("caption") or ""
            ts = m.get("timestamp") or ""
            perm = m.get("permalink") or ""
            murl = m.get("media_url") or m.get("thumbnail_url") or ""
            mtype = "video" if str(m.get("media_type", "")).upper() == "VIDEO" else "image"

            if murl:
                bundle["media_items"].append({
                    "filename": f"ig_{mid}.{'mp4' if mtype == 'video' else 'jpg'}",
                    "media_type": mtype,
                    "source_url": murl,
                    "alt_text": caption[:200],
                    "post_external_id": mid,
                    "published_at": ts,
                    "metadata": {"platform": "instagram_pro", "permalink": perm},
                })
            mentions = extract_mentions(caption)
            hashtags = extract_hashtags(caption)
            bundle["posts"].append({
                "external_id": mid,
                "platform": "instagram_pro",
                "author": uname,
                "content": caption,
                "published_at": ts,
                "url": perm,
                "media_urls": [murl] if murl else [],
                "mentions": mentions,
                "hashtags": hashtags,
                "metadata": {"like_count": m.get("like_count", 0), "comments_count": m.get("comments_count", 0)},
            })
            for mn in mentions:
                add_interaction(bundle["interactions"], uname, mn, "mention", weight=1.0, metadata={"platform": "instagram_pro"})

            if include_reactions and (m.get("like_count") or 0) > 0:
                bundle["reactions"].append({
                    "post_external_id": mid,
                    "platform": "instagram_pro",
                    "reaction_type": "like",
                    "actor_handle": "",
                    "count": int(m["like_count"]),
                })

            # Comentarios y respuestas en publicaciones propias
            if include_replies and (m.get("comments_count") or 0) > 0:
                r_com = requests.get(
                    f"{META_GRAPH_API}/{mid}/comments",
                    params={"fields": "id,text,username,timestamp,replies{id,text,username,timestamp}", "limit": 25, "access_token": access_token},
                    headers={"User-Agent": USER_AGENT},
                    timeout=timeout,
                )
                if r_com.status_code == 200:
                    for cm in (r_com.json() or {}).get("data") or []:
                        c_author = (cm.get("username") or "").lower()
                        c_text = cm.get("text") or ""
                        if c_author and c_text:
                            bundle["comments"].append({
                                "external_id": str(cm.get("id") or ""),
                                "post_external_id": mid,
                                "author_handle": c_author,
                                "target_handle": uname,
                                "content": c_text,
                                "published_at": cm.get("timestamp") or "",
                                "metadata": {"platform": "instagram_pro", "url": perm},
                            })
                            add_interaction(bundle["interactions"], c_author, uname, "comment", weight=1.6, metadata={"platform": "instagram_pro"})
                        for rep in ((cm.get("replies") or {}).get("data") or []):
                            r_auth = (rep.get("username") or "").lower()
                            r_txt = rep.get("text") or ""
                            if r_auth and r_txt:
                                r_tgt = c_author if r_auth == uname else uname
                                bundle["comments"].append({
                                    "external_id": str(rep.get("id") or ""),
                                    "post_external_id": mid,
                                    "author_handle": r_auth,
                                    "target_handle": r_tgt,
                                    "content": r_txt,
                                    "published_at": rep.get("timestamp") or "",
                                    "metadata": {"platform": "instagram_pro", "url": perm},
                                })
                                add_interaction(bundle["interactions"], r_auth, r_tgt, "reply", weight=1.8, metadata={"platform": "instagram_pro"})
        return bundle

    # Caso B: Business Discovery sobre otra cuenta pública Profesional (Business/Creator)
    fields = (
        f"business_discovery.username({target_user}){{"
        "username,name,biography,website,profile_picture_url,followers_count,follows_count,media_count,"
        f"media.limit({lim}){{id,caption,media_type,media_url,permalink,timestamp,like_count,comments_count}}"
        "}"
    )
    r_bd = requests.get(
        f"{META_GRAPH_API}/{own_ig_id}",
        params={"fields": fields, "access_token": access_token},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    if r_bd.status_code != 200:
        bundle["warnings"].append(
            f"Business Discovery no encontró '@{target_user}' como cuenta Profesional pública (HTTP {r_bd.status_code}). "
            "Meta API solo permite consultar otras cuentas de tipo Business o Creator públicas."
        )
        return bundle

    bd = (r_bd.json() or {}).get("business_discovery") or {}
    if not bd:
        bundle["warnings"].append(f"La cuenta '@{target_user}' no está disponible en Business Discovery.")
        return bundle

    bio = bd.get("biography") or ""
    bundle["profile_links"] = extract_urls(f"{bio} {bd.get('website') or ''}")
    bundle["profile"] = {
        "site": "Instagram",
        "url": f"https://www.instagram.com/{target_user}/",
        "username": target_user,
        "display_name": bd.get("name") or target_user,
        "bio": bio,
        "location": "",
        "avatar_url": bd.get("profile_picture_url") or "",
        "website": bd.get("website") or "",
        "sources": "Meta-BusinessDiscovery",
        "osint_confidence": "ALTA",
        "metadata": {
            "followers_count": bd.get("followers_count"),
            "follows_count": bd.get("follows_count"),
            "media_count": bd.get("media_count"),
        },
    }
    for m in ((bd.get("media") or {}).get("data") or []):
        mid = str(m.get("id") or "")
        caption = m.get("caption") or ""
        ts = m.get("timestamp") or ""
        perm = m.get("permalink") or ""
        murl = m.get("media_url") or ""
        if murl:
            bundle["media_items"].append({
                "filename": f"ig_bd_{mid}.jpg",
                "media_type": "image",
                "source_url": murl,
                "alt_text": caption[:200],
                "post_external_id": mid,
                "published_at": ts,
                "metadata": {"platform": "instagram_pro", "permalink": perm},
            })
        mentions = extract_mentions(caption)
        bundle["posts"].append({
            "external_id": mid,
            "platform": "instagram_pro",
            "author": target_user,
            "content": caption,
            "published_at": ts,
            "url": perm,
            "media_urls": [murl] if murl else [],
            "mentions": mentions,
            "hashtags": extract_hashtags(caption),
            "metadata": {"like_count": m.get("like_count", 0), "comments_count": m.get("comments_count", 0)},
        })
        for mn in mentions:
            add_interaction(bundle["interactions"], target_user, mn, "mention", weight=1.0, metadata={"platform": "instagram_pro"})
        if include_reactions and (m.get("like_count") or 0) > 0:
            bundle["reactions"].append({
                "post_external_id": mid,
                "platform": "instagram_pro",
                "reaction_type": "like",
                "actor_handle": "",
                "count": int(m["like_count"]),
            })

    bundle["warnings"].append(
        "Aviso de plataforma: Instagram Business Discovery entrega perfil, bio y publicaciones de terceros Profesionales, "
        "pero Meta no expone comentarios ni lista de seguidores de terceros."
    )
    return bundle


# Para qué sirve: extrae información, publicaciones, comentarios y reacciones de una Página de Facebook administrada por OAuth2.
# Cómo funciona: lista Páginas en `/me/accounts`, usa su token de página para leer `/{page_id}/posts` y normaliza al `ConnectorBundle`.
def fetch_facebook_page(
    handle: str,
    limit: int = 20,
    include_replies: bool = True,
    include_reactions: bool = True,
    access_token: Optional[str] = None,
    timeout: int = 12,
) -> dict:
    """Consulta una Página de Facebook administrada por el usuario vía Meta Graph API oficial (OAuth2)."""
    target = (handle or "").strip().lstrip("@")
    bundle = empty_bundle("facebook_page", target, access_method="oauth2_authorized")
    if not access_token:
        bundle["warnings"].append(
            "Facebook Graph API solo permite consultar Páginas administradas mediante OAuth2 oficial "
            "(no perfiles personales de terceros). Para tu perfil personal, usa el importador de exportación Meta JSON/ZIP."
        )
        return bundle

    r_pgs = requests.get(
        f"{META_GRAPH_API}/me/accounts",
        params={"fields": "id,name,username,about,link,website,access_token,picture{url}", "access_token": access_token},
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    if r_pgs.status_code != 200:
        bundle["warnings"].append(f"No se pudieron listar Páginas administradas en Facebook (HTTP {r_pgs.status_code}).")
        return bundle

    pages = (r_pgs.json() or {}).get("data") or []
    chosen = None
    for pg in pages:
        if not target or target.lower() in (str(pg.get("id", "")).lower(), (pg.get("username") or "").lower(), (pg.get("name") or "").lower()):
            chosen = pg
            break
    if not chosen and pages:
        chosen = pages[0]
    if not chosen:
        bundle["warnings"].append("No se encontraron Páginas de Facebook administradas en esta conexión OAuth2.")
        return bundle

    page_id = str(chosen["id"])
    page_token = chosen.get("access_token") or access_token
    page_handle = (chosen.get("username") or chosen.get("name") or page_id).lower().replace(" ", "_")
    bio = chosen.get("about") or ""
    bundle["handle"] = page_handle
    bundle["profile_links"] = extract_urls(f"{bio} {chosen.get('website') or ''}")
    bundle["profile"] = {
        "site": "Facebook Page",
        "url": chosen.get("link") or f"https://www.facebook.com/{page_id}",
        "username": page_handle,
        "display_name": chosen.get("name") or page_handle,
        "bio": bio,
        "location": "",
        "avatar_url": ((chosen.get("picture") or {}).get("data") or {}).get("url") or "",
        "website": chosen.get("website") or "",
        "sources": "Meta-Graph-OAuth2",
        "osint_confidence": "ALTA",
        "metadata": {"page_id": page_id},
    }

    r_posts = requests.get(
        f"{META_GRAPH_API}/{page_id}/posts",
        params={
            "fields": "id,message,created_time,permalink_url,full_picture,place{name},reactions.summary(total_count),comments.limit(20){id,message,created_time,from{id,name}}",
            "limit": max(1, min(int(limit), 25)),
            "access_token": page_token,
        },
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
    )
    if r_posts.status_code != 200:
        bundle["warnings"].append(f"No se pudieron leer publicaciones de la Página (HTTP {r_posts.status_code}).")
        return bundle

    for p in (r_posts.json() or {}).get("data") or []:
        pid = str(p.get("id") or "")
        msg = p.get("message") or ""
        ts = p.get("created_time") or ""
        perm = p.get("permalink_url") or ""
        pic = p.get("full_picture") or ""
        place_name = (p.get("place") or {}).get("name") or ""
        if pic:
            bundle["media_items"].append({
                "filename": f"fb_{pid}.jpg",
                "media_type": "image",
                "source_url": pic,
                "alt_text": msg[:200],
                "post_external_id": pid,
                "published_at": ts,
                "metadata": {"platform": "facebook_page", "permalink": perm},
            })
        bundle["posts"].append({
            "external_id": pid,
            "platform": "facebook_page",
            "author": page_handle,
            "content": msg,
            "published_at": ts,
            "url": perm,
            "published_location": place_name,
            "media_urls": [pic] if pic else [],
            "mentions": extract_mentions(msg),
            "hashtags": extract_hashtags(msg),
            "metadata": {"place": place_name},
        })
        if include_reactions:
            total_rx = (((p.get("reactions") or {}).get("summary") or {}).get("total_count")) or 0
            if total_rx > 0:
                bundle["reactions"].append({
                    "post_external_id": pid,
                    "platform": "facebook_page",
                    "reaction_type": "reaction_total",
                    "actor_handle": "",
                    "count": int(total_rx),
                })
        if include_replies:
            for cm in ((p.get("comments") or {}).get("data") or []):
                c_msg = cm.get("message") or ""
                c_from = ((cm.get("from") or {}).get("name") or "usuario_fb").strip().lower().replace(" ", "_")
                if c_msg:
                    bundle["comments"].append({
                        "external_id": str(cm.get("id") or ""),
                        "post_external_id": pid,
                        "author_handle": c_from,
                        "target_handle": page_handle,
                        "content": c_msg,
                        "published_at": cm.get("created_time") or "",
                        "metadata": {"platform": "facebook_page", "url": perm},
                    })
                    add_interaction(bundle["interactions"], c_from, page_handle, "comment", weight=1.6, metadata={"platform": "facebook_page"})
    return bundle
