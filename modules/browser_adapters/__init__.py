# -*- coding: utf-8 -*-
"""Registro de adaptadores para el Agente de Navegación Supervisada."""
from modules.browser_adapters.base import BaseBrowserAdapter
from modules.browser_adapters.facebook import FacebookBrowserAdapter
from modules.browser_adapters.instagram import InstagramBrowserAdapter

BROWSER_ADAPTERS = {
    "instagram": InstagramBrowserAdapter,
    "facebook": FacebookBrowserAdapter,
}


def get_browser_adapter(platform: str) -> BaseBrowserAdapter:
    key = (platform or "").strip().lower()
    if key not in BROWSER_ADAPTERS:
        raise ValueError(f"Adaptador de navegador no disponible para '{platform}'. Disponibles: {', '.join(BROWSER_ADAPTERS)}")
    return BROWSER_ADAPTERS[key]()


__all__ = ["BROWSER_ADAPTERS", "BaseBrowserAdapter", "get_browser_adapter"]
