# -*- coding: utf-8 -*-
"""
Cliente HTTP unificado con caché TTL en SQLite (`http_cache`) y control de rate-limit (`Retry-After`).
Evita repetir consultas idénticas a fuentes públicas (XposedOrNot, HIBP, Nominatim, etc.)
dentro de la misma sesión/caso y aplica backoff homogéneo ante HTTP 429/503.
"""
import email.utils
import time
from typing import Callable, Optional

from modules import storage
from modules.logger import get_logger

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    requests = None  # type: ignore[assignment]
    REQUESTS_OK = False

_LOGGER = get_logger("http_client")


def parse_retry_after(header_val: Optional[str], default_wait: float = 1.5, max_wait: float = 12.0) -> float:
    """Interpreta cabecera `Retry-After` (segundos o fecha HTTP-date), acotada a `[0.05, max_wait]`."""
    raw = (header_val or "").strip()
    wait = float(default_wait)
    if raw:
        if raw.isdigit():
            wait = float(int(raw))
        else:
            try:
                dt = email.utils.parsedate_to_datetime(raw)
                if dt is not None:
                    wait = max(0.1, dt.timestamp() - time.time())
            except Exception:  # noqa: BLE001
                wait = float(default_wait)
    return max(0.05, min(wait, float(max_wait)))


def cached_get_json(
    source: str,
    url: str,
    query_key: str = "",
    *,
    headers: Optional[dict] = None,
    params: Optional[dict] = None,
    timeout: int = 12,
    ttl_seconds: int = 1800,
    negative_ttl_seconds: int = 600,
    max_retries: int = 2,
    requester: Optional[Callable] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
    use_cache: bool = True,
) -> dict:
    """
    Ejecuta GET JSON con:
      1) Búsqueda previa en caché SQLite `http_cache` por `(source, query_key or url)`.
      2) Reintentos con backoff y respeto a `Retry-After` ante HTTP 429 o 503.
      3) Persistencia automática en `http_cache` para respuestas 200 y 404.
    """
    qkey = (query_key or url).strip()
    if use_cache and ttl_seconds > 0:
        hit = storage.get_http_cache(source, qkey)
        if hit is not None:
            return {
                "ok": hit["status_code"] == 200,
                "status_code": hit["status_code"],
                "data": hit["payload"],
                "cached": True,
                "error": None,
            }

    req_fn = requester or (requests.get if REQUESTS_OK else None)
    if req_fn is None:
        return {"ok": False, "status_code": 0, "data": None, "cached": False, "error": "requests no instalado"}

    sleeper = sleep_fn or time.sleep
    last_resp = None
    last_exc = None

    for attempt in range(max_retries + 1):
        try:
            resp = req_fn(url, headers=headers, params=params, timeout=timeout)
            last_resp = resp
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < max_retries:
                wait = min(1.0 * (2 ** attempt), 6.0)
                _LOGGER.info("Reintento %s/%s por error de red en %s: %s", attempt + 1, max_retries, source, exc)
                sleeper(wait)
                continue
            return {
                "ok": False,
                "status_code": 0,
                "data": None,
                "cached": False,
                "error": f"Error de red ({source}): {exc}",
            }

        if resp.status_code in (429, 503) and attempt < max_retries:
            wait = parse_retry_after(resp.headers.get("Retry-After"), default_wait=1.2 * (2 ** attempt))
            _LOGGER.warning("Rate-limit/disponibilidad HTTP %s en %s; esperando %.2fs", resp.status_code, source, wait)
            sleeper(wait)
            continue
        break

    if last_resp is None:
        return {"ok": False, "status_code": 0, "data": None, "cached": False, "error": str(last_exc or "Error desconocido")}

    st = int(last_resp.status_code)
    if st == 404:
        if use_cache and negative_ttl_seconds > 0:
            storage.set_http_cache(source, qkey, 404, None, ttl_seconds=negative_ttl_seconds)
        return {"ok": False, "status_code": 404, "data": None, "cached": False, "error": None}

    if st != 200:
        return {"ok": False, "status_code": st, "data": None, "cached": False, "error": f"HTTP {st}"}

    try:
        payload = last_resp.json()
    except Exception:  # noqa: BLE001
        return {"ok": False, "status_code": st, "data": None, "cached": False, "error": "Respuesta JSON inválida"}

    if use_cache and ttl_seconds > 0:
        storage.set_http_cache(source, qkey, 200, payload, ttl_seconds=ttl_seconds)
    return {"ok": True, "status_code": 200, "data": payload, "cached": False, "error": None}
