# -*- coding: utf-8 -*-
"""Registro de adaptadores para el Agente de Navegación Supervisada."""
from modules.browser_adapters.base import BaseBrowserAdapter
from modules.browser_adapters.facebook import FacebookBrowserAdapter
from modules.browser_adapters.instagram import InstagramBrowserAdapter

BROWSER_ADAPTERS = {
    "instagram": InstagramBrowserAdapter,
    "facebook": FacebookBrowserAdapter,
}


# Para qué sirve: entrega el adaptador DOM supervisado correspondiente a la plataforma solicitada.
# Cómo funciona: busca la clave normalizada en `BROWSER_ADAPTERS` y devuelve una nueva instancia (o lanza `ValueError`).
def get_browser_adapter(platform: str) -> BaseBrowserAdapter:
    """Devuelve la instancia de adaptador DOM supervisado para la plataforma solicitada."""
    key = (platform or "").strip().lower()
    if key not in BROWSER_ADAPTERS:
        raise ValueError(f"Adaptador de navegador no disponible para '{platform}'. Disponibles: {', '.join(BROWSER_ADAPTERS)}")
    return BROWSER_ADAPTERS[key]()


__all__ = ["BROWSER_ADAPTERS", "BaseBrowserAdapter", "get_browser_adapter"]
