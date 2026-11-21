# -*- coding: utf-8 -*-
"""
Perfiles declarativos de selectores DOM/ARIA y reglas de validación visual por plataforma.
Centraliza los selectores de Facebook e Instagram y permite sobrescritura opcional vía
`data/browser_selectors_override.json` sin editar código de los adaptadores.
"""
import copy
import json
from pathlib import Path
from typing import Optional

from modules.logger import get_logger

_LOGGER = get_logger("browser_adapters.selectors")
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
OVERRIDE_FILE = ROOT_DIR / "data" / "browser_selectors_override.json"

PLATFORM_SELECTOR_PROFILES = {
    "facebook": {
        "version": "2026-11",
        "profile": {
            "main_selectors": ['[role="main"]', "body"],
            "heading_selectors": ["h1", '[role="heading"]'],
            "external_link_selector": 'a[href^="http"]',
        },
        "posts": {
            "article_selector": '[role="article"]',
            "message_selectors": ['[data-ad-preview="message"]', 'div[dir="auto"]'],
            "permalink_selectors": ['a[href*="/posts/"]', 'a[href*="/permalink/"]', 'a[href*="story_fbid"]'],
            "image_selector": 'img[src*="scontent"]',
        },
        "comment_button": {
            "forbidden_regex": r"me gusta|no me gusta|dislike|\blike\b|reacci|react|compartir|share|enviar|send|ocultar|hide|report|eliminar|delete",
            "min_width": 20,
            "min_height": 20,
            "icon_selectors": ['i[data-visualcompletion="css-img"]', "svg"],
            "strategies": [
                {
                    "id": "fb_data_ad_comment_button",
                    "kind": "child_selector",
                    "selector": '[data-ad-rendering-role="comment_button"]',
                },
                {
                    "id": "fb_aria_comment_with_icon",
                    "kind": "aria_regex_with_icon",
                    "pattern": r"^(comentar|comment|dejar un comentario|leave a comment)",
                },
            ],
            "dialog_selector": '[role="dialog"]',
            "expand_more_keywords": [
                "ver más comentarios",
                "view more comments",
                "ver los",
                "ver respuestas",
                "view replies",
            ],
            "expand_forbidden": ["me gusta", "like", "dislike", "reacci", "compartir", "share"],
        },
        "comments": {
            "reply_container_selectors": ['div[aria-label*="Respuesta"]', 'div[aria-label*="Reply"]'],
            "author_link": 'a[role="link"]',
            "author_name": 'a[role="link"] span, strong',
            "body_selector": 'div[dir="auto"]',
        },
    },
    "instagram": {
        "version": "2026-11",
        "profile": {
            "header_selector": "header",
            "heading_selector": "h1, h2",
            "avatar_selector": "img",
            "links_selector": "a[href]",
        },
        "posts": {
            "post_link_selector": "a[href*='/p/'], a[href*='/reel/']",
            "article_selectors": ["article", "main", "body"],
            "time_selector": "time",
            "image_selector": "img",
        },
        "expand_comments": {
            "button_selectors": ['button', 'div[role="button"]', 'span[role="button"]'],
            "keywords": ["ver los", "view all", "cargar más", "load more"],
        },
        "caption_strategies": [
            {"id": "ig_caption_h1", "selector": "h1"},
            {"id": "ig_caption_og_meta", "selector": 'meta[property="og:description"], meta[name="description"]'},
            {"id": "ig_caption_span_auto", "selector": 'span[dir="auto"]'},
        ],
        "comment_strategies": [
            {"id": "ig_comments_ul_li", "selector": "ul li"},
            {"id": "ig_comments_div_rows", "selector": 'a[href^="/"]'},
        ],
        "ui_noise_words": [
            "no te pierdas ninguna publicación",
            "iniciar sesión",
            "regístrate",
            "responder",
            "reply",
            "me gusta",
            "like",
            "ver traducción",
            "see translation",
            "seguir",
            "following",
            "editado",
            "ocultar",
            "ver respuestas",
            "view replies",
        ],
        "reserved_paths": ["p", "reel", "reels", "explore", "accounts", "stories", "direct", "tv", "about", "legal"],
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def get_platform_selectors(platform: str, override_path: Optional[Path] = None) -> dict:
    """
    Devuelve el perfil declarativo de selectores para `platform`, fusionando
    cualquier override local en `data/browser_selectors_override.json` si existe.
    """
    plat = (platform or "").strip().lower()
    profile = copy.deepcopy(PLATFORM_SELECTOR_PROFILES.get(plat) or {})
    ov_file = override_path or OVERRIDE_FILE
    if ov_file and ov_file.is_file():
        try:
            raw = json.loads(ov_file.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get(plat), dict):
                profile = _deep_merge(profile, raw[plat])
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("No se pudo cargar %s: %s", ov_file, exc)
    return profile
