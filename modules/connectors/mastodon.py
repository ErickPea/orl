# -*- coding: utf-8 -*-
"""
Conector oficial de API pública para Mastodon / Fediverso (ActivityPub).
Acepta `@usuario@instancia`, `usuario@instancia` o URL de perfil (`https://mastodon.social/@usuario`).
"""
from urllib.parse import urlparse

import requests

from modules.connectors.common import (
    USER_AGENT,
    add_interaction,
    empty_bundle,
    extract_hashtags,
    extract_mentions,
    extract_urls,
    html_to_text,
)


# Para qué sirve: separa un handle o URL del Fediverso en tupla `(username, instance_host)`.
# Cómo funciona: extrae host y usuario de URLs o cadenas `user@host`, usando `mastodon.social` por defecto.
def _parse_mastodon_handle(raw: str) -> tuple:
    """Devuelve (username, instance_host). Por defecto usa `mastodon.social` si no se indica instancia."""
    val = (raw or "").strip()
    if not val:
        return "", ""
    if val.startswith("http://") or val.startswith("https://"):
        p = urlparse(val)
        host = (p.netloc or "").lower()
        parts = [s for s in p.path.split("/") if s]
        user = parts[0].lstrip("@") if parts else ""
        return user, host
    cleaned = val.lstrip("@")
    if "@" in cleaned:
        user, host = cleaned.split("@", 1)
        return user.strip(), host.strip().lower()
    return cleaned.strip(), "mastodon.social"


# Para qué sirve: extrae perfil, campos verificados, estados, adjuntos, respuestas y favoritos en Mastodon.
# Cómo funciona: consulta `/api/v1/accounts/lookup`, `/statuses`, `/context` y `/favourited_by` limpiando HTML con `html_to_text`.
def fetch_mastodon(
    handle: str,
    limit: int = 25,
    include_replies: bool = True,
    include_reactions: bool = False,
    timeout: int = 12,
) -> dict:
    """Obtiene perfil público, bio, campos verificados, estados, adjuntos, respuestas y favoritos en Mastodon."""
    user, instance = _parse_mastodon_handle(handle)
    acct_full = f"{user}@{instance}" if user and instance else handle
    bundle = empty_bundle("mastodon", acct_full)
    if not user or not instance:
        bundle["warnings"].append("Indica un handle válido de Mastodon (ej. @usuario@mastodon.social).")
        return bundle

    base = f"https://{instance}/api/v1"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}

    # 1) Lookup de cuenta pública
    try:
        r_acc = requests.get(f"{base}/accounts/lookup", params={"acct": user}, headers=headers, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        bundle["warnings"].append(f"No se pudo conectar a la instancia {instance}: {exc}")
        return bundle

    if r_acc.status_code != 200:
        bundle["warnings"].append(f"Cuenta no encontrada en {instance} (HTTP {r_acc.status_code}).")
        return bundle

    acc = r_acc.json() or {}
    acc_id = str(acc.get("id") or "")
    bio_text = html_to_text(acc.get("note") or "")
    fields = acc.get("fields") or []
    website = ""
    extra_fields = {}
    for f in fields:
        fname = html_to_text(f.get("name") or "")
        fval = html_to_text(f.get("value") or "")
        if fname:
            extra_fields[fname] = fval
        if not website and ("http://" in fval or "https://" in fval):
            website = fval

    bundle["profile_links"] = extract_urls(f"{bio_text} {website} {' '.join(extra_fields.values())}")

    bundle["profile"] = {
        "site": f"Mastodon ({instance})",
        "url": acc.get("url") or f"https://{instance}/@{user}",
        "username": user,
        "display_name": acc.get("display_name") or user,
        "bio": bio_text,
        "location": "",
        "avatar_url": acc.get("avatar_static") or acc.get("avatar") or "",
        "website": website,
        "sources": "Mastodon-API",
        "osint_confidence": "ALTA",
        "metadata": {
            "instance": instance,
            "acct": acct_full,
            "followers_count": acc.get("followers_count"),
            "following_count": acc.get("following_count"),
            "statuses_count": acc.get("statuses_count"),
            "fields": extra_fields,
        },
    }

    if not acc_id:
        return bundle

    # 2) Publicaciones públicas (statuses)
    try:
        r_st = requests.get(
            f"{base}/accounts/{acc_id}/statuses",
            params={"limit": max(1, min(int(limit), 40))},
            headers=headers,
            timeout=timeout,
        )
    except Exception as exc:  # noqa: BLE001
        bundle["warnings"].append(f"Error consultando publicaciones de Mastodon: {exc}")
        return bundle

    if r_st.status_code != 200:
        bundle["warnings"].append(f"No se pudieron leer publicaciones públicas (HTTP {r_st.status_code}).")
        return bundle

    statuses = r_st.json() or []
    context_ids = []

    for st in statuses:
        st_id = str(st.get("id") or "")
        reblog = st.get("reblog")
        target_st = reblog or st
        text = html_to_text(target_st.get("content") or "")
        created_at = target_st.get("created_at") or ""
        st_url = target_st.get("url") or ""
        author = ((target_st.get("account") or {}).get("acct") or acct_full).lower()

        mentions = [
            (m.get("acct") or m.get("username") or "").lower()
            for m in (target_st.get("mentions") or [])
            if m.get("acct") or m.get("username")
        ]
        for em in extract_mentions(text):
            if em not in mentions:
                mentions.append(em)

        hashtags = [(t.get("name") or "").lower() for t in (target_st.get("tags") or []) if t.get("name")]
        for eh in extract_hashtags(text):
            if eh not in hashtags:
                hashtags.append(eh)

        media_urls = []
        for idx, att in enumerate(target_st.get("media_attachments") or []):
            murl = att.get("url") or att.get("preview_url") or ""
            mtype = "video" if str(att.get("type", "")).lower() in ("video", "gifv") else "image"
            if murl:
                media_urls.append(murl)
                bundle["media_items"].append({
                    "filename": f"masto_{st_id}_{idx + 1}.{'mp4' if mtype == 'video' else 'jpg'}",
                    "media_type": mtype,
                    "source_url": murl,
                    "alt_text": att.get("description") or "",
                    "post_external_id": st_id,
                    "published_at": created_at,
                    "metadata": {"platform": "mastodon", "instance": instance},
                })

        bundle["posts"].append({
            "external_id": st_id,
            "platform": "mastodon",
            "author": author,
            "content": text,
            "published_at": created_at,
            "url": st_url,
            "media_urls": media_urls,
            "mentions": mentions,
            "hashtags": hashtags,
            "metadata": {
                "instance": instance,
                "is_reblog": bool(reblog),
                "favourites_count": target_st.get("favourites_count", 0),
                "reblogs_count": target_st.get("reblogs_count", 0),
                "replies_count": target_st.get("replies_count", 0),
            },
        })

        for m in mentions:
            add_interaction(bundle["interactions"], acct_full, m, "mention", weight=1.0, metadata={"platform": "mastodon"})

        if include_replies and not reblog and (st.get("replies_count") or 0) > 0 and st_id:
            context_ids.append(st_id)

    # 3) Respuestas públicas en los últimos 5 estados propios con conversación
    for st_id in context_ids[:5]:
        try:
            r_ctx = requests.get(f"{base}/statuses/{st_id}/context", headers=headers, timeout=timeout)
            if r_ctx.status_code != 200:
                continue
            desc = (r_ctx.json() or {}).get("descendants") or []
            for rep in desc[:15]:
                rauthor = ((rep.get("account") or {}).get("acct") or "").lower()
                rtext = html_to_text(rep.get("content") or "")
                rid = str(rep.get("id") or "")
                if not rauthor or not rtext:
                    continue
                bundle["comments"].append({
                    "external_id": rid,
                    "post_external_id": st_id,
                    "author_handle": rauthor,
                    "target_handle": acct_full.lower(),
                    "content": rtext,
                    "published_at": rep.get("created_at") or "",
                    "metadata": {"platform": "mastodon", "url": rep.get("url") or ""},
                })
                add_interaction(bundle["interactions"], rauthor, acct_full, "comment", weight=1.5, metadata={"platform": "mastodon"})
        except Exception:  # noqa: BLE001
            continue

    # 4) Favoritos públicos en los últimos 5 estados propios (opcional)
    if include_reactions:
        own_ids = [p["external_id"] for p in bundle["posts"] if p.get("external_id") and not (p.get("metadata") or {}).get("is_reblog")][:5]
        for st_id in own_ids:
            try:
                r_fav = requests.get(f"{base}/statuses/{st_id}/favourited_by", params={"limit": 20}, headers=headers, timeout=timeout)
                if r_fav.status_code != 200:
                    continue
                for fav_acc in r_fav.json() or []:
                    actor_h = (fav_acc.get("acct") or fav_acc.get("username") or "").lower()
                    if actor_h and actor_h != acct_full.lower():
                        bundle["reactions"].append({
                            "post_external_id": st_id,
                            "platform": "mastodon",
                            "reaction_type": "favourite",
                            "actor_handle": actor_h,
                            "count": 1,
                        })
                        add_interaction(bundle["interactions"], actor_h, acct_full, "like", weight=0.6, metadata={"platform": "mastodon"})
            except Exception:  # noqa: BLE001
                continue

    return bundle
