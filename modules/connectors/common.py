# -*- coding: utf-8 -*-
"""Utilidades compartidas y contrato unificado para conectores autorizados (Fase 2 + Investigación de Cuenta)."""
import html
import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Optional

USER_AGENT = "OSINT-Auto/2.4 (authorized-public-connector)"

_MENTION_RE = re.compile(r"(?<![A-Za-z0-9_])@([A-Za-z0-9_.-]{2,60})")
_REDDIT_USER_RE = re.compile(r"(?<![A-Za-z0-9_])u/([A-Za-z0-9_-]{2,40})", re.I)
_HASHTAG_RE = re.compile(r"(?<![A-Za-z0-9_])#([A-Za-z0-9_\u00C0-\u024F]{2,50})")
_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)


# Para qué sirve: devuelve la marca de tiempo actual estandarizada para sellar capturas de conectores.
# Cómo funciona: formatea `datetime.now()` en `YYYY-MM-DD HH:MM:SS`.
def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class _HtmlTextExtractor(HTMLParser):
    """Convierte HTML simple (ej. Mastodon) a texto plano preservando saltos de línea."""

    # Para qué sirve: inicializa la lista acumuladora de fragmentos de texto del parser HTML.
    # Cómo funciona: invoca `super().__init__()` y crea `self.parts = []`.
    def __init__(self):
        super().__init__()
        self.parts = []

    # Para qué sirve: inserta saltos de línea al abrir etiquetas de bloque o salto HTML.
    # Cómo funciona: agrega `\n` a `self.parts` cuando encuentra `br`, `p`, `div` o `li`.
    def handle_starttag(self, tag, attrs):
        if tag.lower() in ("br", "p", "div", "li"):
            self.parts.append("\n")

    # Para qué sirve: captura cada segmento textual encontrado dentro del HTML.
    # Cómo funciona: añade `data` tal cual a `self.parts`.
    def handle_data(self, data):
        self.parts.append(data)

    # Para qué sirve: reconstruye el texto plano limpio preservando párrafos sin espacios redundantes.
    # Cómo funciona: decodifica entidades con `html.unescape`, colapsa espacios por línea y filtra líneas vacías.
    def get_text(self) -> str:
        raw = html.unescape("".join(self.parts))
        lines = [" ".join(line.split()) for line in raw.splitlines()]
        return "\n".join(line for line in lines if line).strip()


# Para qué sirve: convierte HTML devuelto por APIs (ej. Mastodon) en texto legible para NLP y reportes.
# Cómo funciona: alimenta `_HtmlTextExtractor` y retorna `parser.get_text()`.
def html_to_text(raw_html: str) -> str:
    if not raw_html:
        return ""
    parser = _HtmlTextExtractor()
    parser.feed(str(raw_html))
    return parser.get_text()


# Para qué sirve: extrae handles mencionados (`@usuario` o `u/usuario`) presentes en publicaciones o bios.
# Cómo funciona: aplica `_MENTION_RE` y `_REDDIT_USER_RE`, limpia puntuación y deduplica en orden.
def extract_mentions(text: str) -> list:
    """Extrae menciones `@usuario` y `u/usuario` normalizadas y sin duplicados."""
    if not text:
        return []
    found = []
    seen = set()
    for regex in (_MENTION_RE, _REDDIT_USER_RE):
        for m in regex.findall(text):
            clean = m.strip(".,;:!?()[]").lower()
            if clean and clean not in seen:
                seen.add(clean)
                found.append(clean)
    return found


# Para qué sirve: extrae hashtags de un texto para perfilado de intereses e ideas.
# Cómo funciona: busca patrones `_HASHTAG_RE`, pasa a minúsculas y evita duplicados.
def extract_hashtags(text: str) -> list:
    """Extrae `#hashtags` normalizados y sin duplicados."""
    if not text:
        return []
    found = []
    seen = set()
    for m in _HASHTAG_RE.findall(text):
        clean = m.strip().lower()
        if clean and clean not in seen:
            seen.add(clean)
            found.append(clean)
    return found


# Para qué sirve: extrae URLs públicas incrustadas en bios o posts para resolución de identidad.
# Cómo funciona: busca `_URL_RE`, recorta signos de cierre y devuelve lista única.
def extract_urls(text: str) -> list:
    """Extrae URLs públicas presentes en una bio o publicación sin duplicados."""
    if not text:
        return []
    found = []
    seen = set()
    for u in _URL_RE.findall(text):
        clean = u.rstrip(".,;:!?")
        if clean and clean not in seen:
            seen.add(clean)
            found.append(clean)
    return found


# Para qué sirve: construye la ficha declarativa de capacidades y límites autorizados de una plataforma.
# Cómo funciona: empaqueta `auth_mode`, banderas `supports_*` y texto `limitations` en un dict estándar.
def build_capabilities(
    platform_id: str,
    name: str,
    auth_mode: str,
    supports_profile: bool = True,
    supports_posts: bool = True,
    supports_comments: bool = True,
    supports_reactions: bool = False,
    supports_third_party_public: bool = True,
    limitations: str = "",
) -> dict:
    """Declara explícitamente las capacidades reales y límites autorizados de una plataforma."""
    return {
        "id": platform_id,
        "name": name,
        "auth_mode": auth_mode,  # 'public_api' | 'api_key' | 'oauth2'
        "supports_profile": supports_profile,
        "supports_posts": supports_posts,
        "supports_comments": supports_comments,
        "supports_reactions": supports_reactions,
        "supports_third_party_public": supports_third_party_public,
        "limitations": limitations,
    }


# Para qué sirve: crea la estructura normalizada vacía (`ConnectorBundle`) que todo conector/importador debe retornar.
# Cómo funciona: inicializa listas de perfil, posts, comentarios, media, reacciones, interacciones, interactuantes y avisos.
def empty_bundle(platform: str, handle: str, access_method: str = "public_api") -> dict:
    """Estructura normalizada devuelta por cualquier conector o importador."""
    return {
        "platform": platform,
        "handle": handle,
        "access_method": access_method,
        "captured_at": now_iso(),
        "profile": None,
        "profile_links": [],
        "posts": [],
        "comments": [],
        "media_items": [],
        "reactions": [],
        "interactions": [],
        "interactants": [],
        "warnings": [],
    }


# Para qué sirve: añade o actualiza un tercero interactuante dentro de `bundle["interactants"]` sin duplicarlo.
# Cómo funciona: normaliza handle/display_name, actualiza `last_seen`/URL si ya existe o inserta registro nuevo.
def add_interactant(
    interactants: list,
    platform: str,
    handle: str = "",
    display_name: str = "",
    profile_url: str = "",
    seen_at: str = "",
) -> None:
    """Registra un interactuante nominal normalizado en el ConnectorBundle sin duplicados."""
    h = (handle or "").strip().lstrip("@").lower()
    dn = (display_name or "").strip()
    if not h and not dn:
        return
    if not h and dn:
        h = re.sub(r"[^a-z0-9_.-]+", "_", dn.lower()).strip("_") or "desconocido"
    plat = (platform or "open").strip().lower()
    for ex in interactants:
        if ex.get("platform") == plat and ex.get("handle") == h:
            if dn and not ex.get("display_name"):
                ex["display_name"] = dn
            if profile_url and not ex.get("profile_url"):
                ex["profile_url"] = profile_url
            if seen_at and (not ex.get("last_seen") or seen_at > ex["last_seen"]):
                ex["last_seen"] = seen_at
            return
    interactants.append({
        "platform": plat,
        "handle": h,
        "display_name": dn or h,
        "profile_url": profile_url or "",
        "first_seen": seen_at or "",
        "last_seen": seen_at or "",
    })


# Para qué sirve: registra una arista dirigida `(origen -> destino)` en el bundle para alimentar el grafo SNA.
# Cómo funciona: normaliza ambos handles, descarta vacíos o auto-bucles (`src == tgt`) y guarda tipo y peso.
def add_interaction(
    interactions: list,
    source_handle: str,
    target_handle: str,
    kind: str,
    weight: float = 1.0,
    metadata: Optional[dict] = None,
) -> None:
    """Añade una interacción normalizada evitando auto-bucles triviales."""
    src = (source_handle or "").strip().lstrip("@").lower()
    tgt = (target_handle or "").strip().lstrip("@").lower()
    if not src or not tgt or src == tgt:
        return
    interactions.append({
        "source_handle": src,
        "target_handle": tgt,
        "kind": kind,
        "weight": float(weight),
        "evidence_count": 1,
        "metadata": metadata or {},
    })
