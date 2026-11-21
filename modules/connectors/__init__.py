# -*- coding: utf-8 -*-
"""
Registro central de conectores autorizados e importador para OSINT-Auto.
Todos los conectores usan exclusivamente APIs públicas oficiales, OAuth2 autorizado
o exportaciones cargadas por el usuario.
"""
import os
from typing import Optional

from modules.connectors.bluesky import fetch_bluesky
from modules.connectors.common import build_capabilities
from modules.connectors.github import fetch_github
from modules.connectors.importer import parse_imported_content, sample_import_template
from modules.connectors.mastodon import fetch_mastodon
from modules.connectors.meta_graph import fetch_facebook_page, fetch_instagram_pro
from modules.connectors.reddit import fetch_reddit
from modules.connectors.youtube import fetch_youtube, youtube_configured

# Conectores de API abierta (compatibilidad Fase 2)
CONNECTORS = {
    "bluesky": {
        "name": "Bluesky (ATProto)",
        "handler": fetch_bluesky,
        "requires_key": False,
        "placeholder": "ej: usuario.bsky.social",
        "description": "Bio completa, publicaciones, fotos con alt-text, respuestas y likes públicos.",
        "capabilities": build_capabilities(
            "bluesky", "Bluesky (ATProto)", "public_api",
            supports_profile=True, supports_posts=True, supports_comments=True,
            supports_reactions=True, supports_third_party_public=True,
            limitations="Acceso público abierto sin login sobre perfiles públicos.",
        ),
    },
    "mastodon": {
        "name": "Mastodon / Fediverso",
        "handler": fetch_mastodon,
        "requires_key": False,
        "placeholder": "ej: @usuario@mastodon.social",
        "description": "Bio, campos verificados, estados, adjuntos multimedia, respuestas y favoritos.",
        "capabilities": build_capabilities(
            "mastodon", "Mastodon / Fediverso", "public_api",
            supports_profile=True, supports_posts=True, supports_comments=True,
            supports_reactions=True, supports_third_party_public=True,
            limitations="Algunas instancias federadas restringen listados de favoritos o contextos.",
        ),
    },
    "reddit": {
        "name": "Reddit (API pública / OAuth2)",
        "handler": fetch_reddit,
        "requires_key": False,
        "placeholder": "ej: spez o u/usuario",
        "description": "Perfil, envíos, imágenes, comentarios propios, respuestas de terceros y puntuación.",
        "capabilities": build_capabilities(
            "reddit", "Reddit (API pública / OAuth2)", "public_api",
            supports_profile=True, supports_posts=True, supports_comments=True,
            supports_reactions=True, supports_third_party_public=True,
            limitations="Sin OAuth2 Reddit puede aplicar rate-limit temporal; con OAuth2 oficial mejora estabilidad.",
        ),
    },
    "github": {
        "name": "GitHub (REST API)",
        "handler": fetch_github,
        "requires_key": False,
        "placeholder": "ej: torvalds",
        "description": "Bio, ubicación, website, repositorios (lenguajes/temas) y actividad colaborativa.",
        "capabilities": build_capabilities(
            "github", "GitHub (REST API)", "public_api",
            supports_profile=True, supports_posts=True, supports_comments=True,
            supports_reactions=False, supports_third_party_public=True,
            limitations="Eventos públicos cubren los últimos 90 días; configura GITHUB_TOKEN para ampliar rate-limit.",
        ),
    },
    "youtube": {
        "name": "YouTube Data API v3",
        "handler": fetch_youtube,
        "requires_key": True,
        "placeholder": "ej: @canal o UC...",
        "description": "Descripción de canal, videos recientes, miniaturas, comentarios y respuestas públicas.",
        "capabilities": build_capabilities(
            "youtube", "YouTube Data API v3", "api_key",
            supports_profile=True, supports_posts=True, supports_comments=True,
            supports_reactions=True, supports_third_party_public=True,
            limitations="Requiere YOUTUBE_API_KEY en .env; solo entrega comentarios públicos habilitados en el canal.",
        ),
    },
}

# Conectores adicionales con OAuth2 oficial (Meta Graph API)
OAUTH_CONNECTORS = {
    "instagram_pro": {
        "name": "Instagram Professional (Meta OAuth2)",
        "handler": fetch_instagram_pro,
        "requires_key": True,
        "placeholder": "ej: @mi_cuenta_business (o cuenta Pro pública)",
        "description": "Cuenta propia Pro (perfil, posts, comentarios y respuestas) o Business Discovery de cuentas Pro públicas.",
        "capabilities": build_capabilities(
            "instagram_pro", "Instagram Professional (Meta Graph API)", "oauth2",
            supports_profile=True, supports_posts=True, supports_comments=True,
            supports_reactions=True, supports_third_party_public=False,
            limitations="Meta Graph API solo entrega comentarios de cuentas Profesionales propias autorizadas por OAuth2. En terceros Business Discovery solo entrega perfil y posts (sin comentarios). No accede a cuentas personales privadas ni personales de terceros.",
        ),
    },
    "facebook_page": {
        "name": "Facebook Page (Meta OAuth2)",
        "handler": fetch_facebook_page,
        "requires_key": True,
        "placeholder": "ej: nombre o ID de Página administrada",
        "description": "Páginas de Facebook administradas por el usuario OAuth2: posts, comentarios, ubicaciones y reacciones.",
        "capabilities": build_capabilities(
            "facebook_page", "Facebook Page (Meta Graph API)", "oauth2",
            supports_profile=True, supports_posts=True, supports_comments=True,
            supports_reactions=True, supports_third_party_public=False,
            limitations="Meta bloquea por diseño el acceso API a perfiles personales de terceros. Solo opera sobre Páginas administradas autorizadas vía OAuth2; para perfiles personales usa el importador oficial Meta JSON/ZIP.",
        ),
    },
}

ALL_PLATFORMS = {**CONNECTORS, **OAUTH_CONNECTORS}


def connectors_catalog() -> list:
    """Devuelve catálogo de los 5 conectores de API abierta (compatibilidad Fase 2)."""
    out = []
    for key, meta in CONNECTORS.items():
        ready = youtube_configured() if key == "youtube" else True
        out.append({
            "id": key,
            "name": meta["name"],
            "requires_key": meta["requires_key"],
            "ready": ready,
            "placeholder": meta["placeholder"],
            "description": meta["description"],
            "github_token": bool(os.environ.get("GITHUB_TOKEN", "").strip()) if key == "github" else None,
        })
    return out


def platforms_capabilities_catalog(active_connections: Optional[list] = None) -> list:
    """
    Devuelve la matriz completa y honesta de capacidades para TODAS las plataformas
    (abiertas + OAuth2 Meta/Reddit), indicando qué está listo hoy y qué requiere OAuth2/API key.
    """
    from modules.platform_auth import oauth_provider_status

    conns = active_connections or []
    oauth_st = oauth_provider_status()
    out = []
    for key, meta in ALL_PLATFORMS.items():
        caps = dict(meta["capabilities"])
        plat_conns = [c for c in conns if c.get("platform") == key and c.get("status") == "active"]
        if key == "youtube":
            ready = youtube_configured()
        elif key in ("instagram_pro", "facebook_page"):
            ready = len(plat_conns) > 0
        else:
            ready = True
        out.append({
            **caps,
            "placeholder": meta["placeholder"],
            "description": meta["description"],
            "ready": ready,
            "oauth_configured": bool((oauth_st.get(key) or {}).get("configured")),
            "active_connections": plat_conns,
        })
    return out


def fetch_from_connector(
    platform: str,
    handle: str,
    limit: int = 25,
    include_replies: bool = True,
    include_reactions: bool = False,
    fetch_post_replies: bool = False,
    access_token: Optional[str] = None,
) -> dict:
    """Ejecuta el conector oficial solicitado pasando solo los argumentos que soporta."""
    key = (platform or "").strip().lower()
    if key not in ALL_PLATFORMS:
        raise ValueError(f"Conector no soportado: {platform}. Disponibles: {', '.join(ALL_PLATFORMS)}")
    handler = ALL_PLATFORMS[key]["handler"]

    if key == "reddit":
        return handler(
            handle=handle,
            limit=limit,
            include_replies=include_replies,
            include_reactions=include_reactions,
            fetch_post_replies=fetch_post_replies,
            access_token=access_token,
        )
    if key in ("bluesky", "mastodon"):
        return handler(
            handle=handle,
            limit=limit,
            include_replies=include_replies,
            include_reactions=include_reactions,
        )
    if key in ("instagram_pro", "facebook_page"):
        return handler(
            handle=handle,
            limit=limit,
            include_replies=include_replies,
            include_reactions=include_reactions,
            access_token=access_token,
        )
    return handler(handle=handle, limit=limit, include_replies=include_replies)


__all__ = [
    "ALL_PLATFORMS",
    "CONNECTORS",
    "OAUTH_CONNECTORS",
    "connectors_catalog",
    "fetch_from_connector",
    "parse_imported_content",
    "platforms_capabilities_catalog",
    "sample_import_template",
]
