# -*- coding: utf-8 -*-
"""
Detección defensiva de exposición en brechas de datos:
  1. XposedOrNot API (gratuita, sin API key) para consultar brechas asociadas a un correo.
  2. Have I Been Pwned (HIBP) API v3 (opcional, requiere variable de entorno HIBP_API_KEY).
  3. HIBP Pwned Passwords con k-Anonymity para auditar si una contraseña PROPIA ha sido
     expuesta en filtraciones (solo envía los primeros 5 caracteres del hash SHA-1 y nunca
     guarda ni registra la contraseña).

Nota de seguridad: este módulo informa metadatos de brechas (nombre, fecha, tipos de datos
expuestos como correos, contraseñas o teléfonos) y recomendaciones defensivas; nunca extrae
ni consulta contraseñas en texto plano de terceros.
"""
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import quote
from . import utils
from .http_client import cached_get_json

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

_USER_AGENT = "OSINT-Auto/2.0 (defensive-breach-check)"

# Palabras clave en `data_classes` para detectar si alguna brecha expuso contraseñas o teléfonos
_KEYWORDS_PASSWORD = ("password", "contraseña", "hash", "credential")
_KEYWORDS_PHONE = ("phone", "teléfono", "telefono", "mobile", "sms")


# Para qué sirve: obtiene la API key de HIBP de forma centralizada.
# Cómo funciona: prioriza el parámetro recibido y, si está vacío, lee `HIBP_API_KEY` del entorno.
def _get_hibp_api_key(api_key: str = None) -> str:
    """Obtiene la API key de HIBP desde el argumento o la variable de entorno HIBP_API_KEY."""
    return (api_key or os.environ.get("HIBP_API_KEY", "")).strip()


# Para qué sirve: consulta brechas de datos asociadas a un correo en Have I Been Pwned API v3 (si hay API key).
# Cómo funciona: usa `cached_get_json` con TTL de 1 h, maneja códigos 404/401/429 y normaliza cada brecha al esquema común.
def check_hibp_email(email: str, api_key: str = None, timeout: int = 12) -> dict:
    """
    Consulta Have I Been Pwned API v3 para un correo electrónico.
    Requiere `HIBP_API_KEY`. Si no está configurada, se omite limpiamente sin error.
    """
    key = _get_hibp_api_key(api_key)
    if not key:
        return {"source": "HIBP", "skipped": True, "breaches": [],
                "note": "HIBP_API_KEY no configurada (se usa XposedOrNot)."}
    if not REQUESTS_OK:
        return {"source": "HIBP", "skipped": True, "breaches": [],
                "error": "requests no instalado"}

    url = (
        f"https://haveibeenpwned.com/api/v3/breachedaccount/"
        f"{quote(email.strip().lower(), safe='')}?truncateResponse=false"
    )
    headers = {
        "hibp-api-key": key,
        "user-agent": _USER_AGENT,
    }
    res = cached_get_json(
        source="hibp",
        url=url,
        query_key=email.strip().lower(),
        headers=headers,
        timeout=timeout,
        ttl_seconds=3600,
        negative_ttl_seconds=900,
        requester=requests.get,
    )
    st = res["status_code"]
    if res.get("error") and st == 0:
        return {"source": "HIBP", "breaches": [], "error": f"Error de red HIBP: {res['error']}"}
    if st == 404:
        return {"source": "HIBP", "breaches": [], "note": "Sin brechas registradas en HIBP."}
    if st == 401:
        return {"source": "HIBP", "breaches": [], "error": "HIBP_API_KEY inválida o expirada (401)."}
    if st == 429:
        return {"source": "HIBP", "breaches": [], "error": "Rate-limit alcanzado en HIBP (429)."}
    if not res["ok"]:
        return {"source": "HIBP", "breaches": [], "error": f"HIBP devolvió HTTP {st}."}

    breaches = []
    for b in (res["data"] or []):
        breaches.append({
            "name": b.get("Name") or b.get("Title") or "Desconocida",
            "domain": b.get("Domain") or "",
            "date": b.get("BreachDate") or "—",
            "data_classes": list(b.get("DataClasses") or []),
            "verified": bool(b.get("IsVerified", False)),
            "sources": ["HIBP"],
        })
    return {"source": "HIBP", "breaches": breaches, "note": f"{len(breaches)} brechas en HIBP."}

# Para qué sirve: unifica las respuestas heterogéneas de XposedOrNot al mismo esquema que HIBP.
# Cómo funciona: procesa `/v1/breach-analytics` y `/v1/check-email`, separa `xposed_data` por `;`/`,` y extrae el nivel de riesgo.
def parse_xposedornot_payload(data: dict) -> dict:
    """
    Normaliza la respuesta JSON de XposedOrNot (`/v1/breach-analytics` o `/v1/check-email`)
    al mismo esquema usado por HIBP para poder deduplicar ambos orígenes.
    """
    if not isinstance(data, dict):
        return {"breaches": [], "risk_label": None, "risk_score": None}
    if str(data.get("Error", "")).lower() == "not found":
        return {"breaches": [], "risk_label": None, "risk_score": None}

    breaches = []
    # Formato rico de /v1/breach-analytics
    exposed = data.get("ExposedBreaches") or {}
    detalles = exposed.get("breaches_details") if isinstance(exposed, dict) else None
    if isinstance(detalles, list):
        for b in detalles:
            if not isinstance(b, dict):
                continue
            raw_data = b.get("xposed_data") or ""
            # XposedOrNot separa los tipos de datos con ';' o ','
            if isinstance(raw_data, list):
                classes = [str(x).strip() for x in raw_data if str(x).strip()]
            else:
                classes = [
                    x.strip()
                    for x in str(raw_data).replace(",", ";").split(";")
                    if x.strip()
                ]
            breaches.append({
                "name": b.get("breach") or "Desconocida",
                "domain": b.get("domain") or "",
                "date": str(b.get("xposed_date") or "—"),
                "data_classes": classes,
                "verified": str(b.get("verified", "")).lower() in ("yes", "true", "1"),
                "password_risk": b.get("password_risk") or "",
                "sources": ["XposedOrNot"],
            })
    # Formato simple de /v1/check-email: {"breaches": [["BreachA", "BreachB"]]}
    elif isinstance(data.get("breaches"), list):
        for item in data["breaches"]:
            nombres = item if isinstance(item, list) else [item]
            for n in nombres:
                if n:
                    breaches.append({
                        "name": str(n),
                        "domain": "",
                        "date": "—",
                        "data_classes": [],
                        "verified": False,
                        "sources": ["XposedOrNot"],
                    })

    # Métrica de riesgo agregada (si XposedOrNot la devuelve)
    risk_label, risk_score = None, None
    metrics = data.get("BreachMetrics") or {}
    risk_list = metrics.get("risk") if isinstance(metrics, dict) else None
    if isinstance(risk_list, list) and risk_list and isinstance(risk_list[0], dict):
        risk_label = risk_list[0].get("risk_label")
        risk_score = risk_list[0].get("risk_score")

    return {"breaches": breaches, "risk_label": risk_label, "risk_score": risk_score}


# Para qué sirve: consulta brechas públicas gratuitas para un correo en XposedOrNot.
# Cómo funciona: llama a `/v1/breach-analytics` usando `cached_get_json` (TTL 1 h) y normaliza con `parse_xposedornot_payload`.
def check_xposedornot_email(email: str, timeout: int = 12) -> dict:
    """
    Consulta la API pública y gratuita de XposedOrNot para detectar si un correo
    aparece en brechas conocidas y qué categorías de datos quedaron expuestas.
    """
    if not REQUESTS_OK:
        return {"source": "XposedOrNot", "breaches": [], "error": "requests no instalado"}

    encoded = quote(email.strip().lower(), safe="")
    url = f"https://api.xposedornot.com/v1/breach-analytics?email={encoded}"
    headers = {"User-Agent": _USER_AGENT}

    res = cached_get_json(
        source="xposedornot",
        url=url,
        query_key=email.strip().lower(),
        headers=headers,
        timeout=timeout,
        ttl_seconds=3600,
        negative_ttl_seconds=900,
        requester=requests.get,
    )
    st = res["status_code"]
    if res.get("error") and st == 0:
        return {"source": "XposedOrNot", "breaches": [], "error": f"Error de red XposedOrNot: {res['error']}"}
    if st == 404:
        return {"source": "XposedOrNot", "breaches": [], "note": "Sin brechas registradas en XposedOrNot."}
    if st == 429:
        return {"source": "XposedOrNot", "breaches": [], "error": "Rate-limit alcanzado en XposedOrNot (429)."}
    if not res["ok"]:
        if res.get("error") == "Respuesta JSON inválida":
            return {"source": "XposedOrNot", "breaches": [], "error": "Respuesta JSON inválida de XposedOrNot."}
        return {"source": "XposedOrNot", "breaches": [], "error": f"XposedOrNot devolvió HTTP {st}."}

    parsed = parse_xposedornot_payload(res["data"])

    return {
        "source": "XposedOrNot",
        "breaches": parsed["breaches"],
        "risk_label": parsed["risk_label"],
        "risk_score": parsed["risk_score"],
        "note": f"{len(parsed['breaches'])} brechas en XposedOrNot.",
    }


# Para qué sirve: evita brechas duplicadas cuando HIBP y XposedOrNot reportan el mismo incidente.
# Cómo funciona: agrupa por nombre alfanumérico en minúscula, combina `sources`/`data_classes` y ordena por fecha descendente.
def _merge_breaches(listas: list) -> list:
    """
    Deduplica brechas entre HIBP y XposedOrNot por nombre normalizado,
    combinando fuentes y tipos de datos expuestos.
    """
    mapa = {}
    for b in listas:
        clave = "".join(c for c in (b.get("name") or "").lower() if c.isalnum())
        if not clave:
            continue
        if clave not in mapa:
            mapa[clave] = {
                "name": b.get("name", "Desconocida"),
                "domain": b.get("domain", ""),
                "date": b.get("date", "—"),
                "data_classes": list(dict.fromkeys(b.get("data_classes") or [])),
                "verified": bool(b.get("verified", False)),
                "sources": list(dict.fromkeys(b.get("sources") or [])),
            }
        else:
            reg = mapa[clave]
            # Combinamos fuentes y tipos de datos; priorizamos metadatos más completos
            for src in b.get("sources") or []:
                if src not in reg["sources"]:
                    reg["sources"].append(src)
            for dc in b.get("data_classes") or []:
                if dc not in reg["data_classes"]:
                    reg["data_classes"].append(dc)
            if not reg["domain"] and b.get("domain"):
                reg["domain"] = b["domain"]
            if reg["date"] in ("", "—") and b.get("date") not in ("", "—"):
                reg["date"] = b["date"]
            reg["verified"] = reg["verified"] or bool(b.get("verified", False))

    # Ordenamos de la brecha más reciente a la más antigua
    return sorted(mapa.values(), key=lambda x: str(x.get("date", "")), reverse=True)


# Para qué sirve: orquesta la auditoría completa de brechas de un correo y genera alertas defensivas.
# Cómo funciona: consulta HIBP + XposedOrNot, deduplica, busca exposición de contraseñas/teléfono y guarda JSON opcional en `out_dir`.
def check_email_breaches(email: str, out_dir: Path = None, api_key: str = None) -> dict:
    """
    Orquesta la verificación de brechas para un correo usando XposedOrNot (siempre)
    y HIBP (si HIBP_API_KEY está configurada). Calcula indicadores clave:
      - `passwords_exposed`: si alguna brecha expuso contraseñas/hashes
      - `phone_exposed`: si alguna brecha expuso números de teléfono
      - `recommendations`: acciones defensivas concretas según lo encontrado
    """
    email_limpio = (email or "").strip().lower()
    utils.info(f"Consultando exposición en brechas para '{email_limpio}'...")

    hibp = check_hibp_email(email_limpio, api_key=api_key)
    xon = check_xposedornot_email(email_limpio)

    breaches = _merge_breaches(hibp.get("breaches", []) + xon.get("breaches", []))
    all_classes = sorted({dc for b in breaches for dc in b.get("data_classes", [])})
    clases_lower = " ".join(all_classes).lower()

    passwords_exposed = any(k in clases_lower for k in _KEYWORDS_PASSWORD)
    phone_exposed = any(k in clases_lower for k in _KEYWORDS_PHONE)

    # Construimos recomendaciones defensivas concretas
    recomendaciones = []
    if breaches:
        if passwords_exposed:
            recomendaciones.append(
                "Cambiar de inmediato la contraseña del correo y de cualquier servicio donde se haya reutilizado."
            )
        recomendaciones.append("Activar autenticación en dos pasos (2FA) con app autenticadora o llave física.")
        if phone_exposed:
            recomendaciones.append(
                "El teléfono figura como dato expuesto en al menos una brecha: extremar alerta ante phishing/SMS y proteger contra SIM-swapping."
            )
    else:
        recomendaciones.append(
            "No se detectaron brechas públicas en las fuentes consultadas; mantener contraseñas únicas y 2FA."
        )

    errores = [x["error"] for x in (hibp, xon) if x.get("error")]
    fuentes_activas = [
        x["source"] for x in (hibp, xon)
        if not x.get("skipped") and not x.get("error")
    ]

    resultado = {
        "email": email_limpio,
        "breached": bool(breaches),
        "breach_count": len(breaches),
        "passwords_exposed": passwords_exposed,
        "phone_exposed": phone_exposed,
        "exposed_data_classes": all_classes,
        "risk_label": xon.get("risk_label"),
        "risk_score": xon.get("risk_score"),
        "breaches": breaches,
        "sources_checked": fuentes_activas,
        "errors": errores,
        "recommendations": recomendaciones,
        "phone_lookup_note": (
            "Los servicios públicos legítimos no permiten buscar brechas directamente por teléfono de terceros; "
            "la exposición telefónica se detecta a partir de los tipos de datos filtrados en las brechas del correo."
        ),
    }

    if out_dir:
        log_path = out_dir / f"breaches_{email_limpio.replace('@', '_at_')}.json"
        log_path.write_text(json.dumps(resultado, indent=2, ensure_ascii=False), encoding="utf-8")
        resultado["log"] = str(log_path)

    if breaches:
        utils.warn(
            f"¡Exposición detectada! {len(breaches)} brechas encontradas "
            f"(contraseñas expuestas: {'SÍ' if passwords_exposed else 'No indicado'} | "
            f"teléfono expuesto: {'SÍ' if phone_exposed else 'No indicado'})."
        )
    elif errores and not fuentes_activas:
        utils.err(f"No se pudo completar la consulta de brechas: {'; '.join(errores)}")
    else:
        utils.ok("Sin brechas públicas detectadas en las fuentes consultadas.")

    return resultado


# Para qué sirve: audita si una contraseña propia aparece en filtraciones sin exponer la clave real ni su hash completo.
# Cómo funciona: envía solo los primeros 5 chars del SHA-1 a `api.pwnedpasswords.com/range` (con `Add-Padding`) y compara el sufijo en local.
def check_password_k_anonymity(password: str, timeout: int = 10) -> dict:
    """
    Audita una contraseña PROPIA usando HIBP Pwned Passwords (protocolo k-Anonymity):
      1. Calcula SHA-1 localmente.
      2. Envía únicamente los primeros 5 caracteres hexadecimales a la API.
      3. Compara localmente el resto del hash (sufijo) contra la lista devuelta.
    La contraseña completa y su hash completo NUNCA salen del equipo ni se guardan en disco.
    """
    if not password:
        return {"compromised": False, "count": 0, "error": "Contraseña vacía."}
    if not REQUESTS_OK:
        return {"compromised": False, "count": 0, "error": "requests no instalado."}

    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()  # noqa: S324 - estándar del protocolo HIBP k-Anonymity
    prefix, suffix = sha1[:5], sha1[5:]

    url = f"https://api.pwnedpasswords.com/range/{prefix}"
    # Add-Padding: true añade ruido de tamaño fijo para impedir análisis de longitud de respuesta
    headers = {"User-Agent": _USER_AGENT, "Add-Padding": "true"}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"compromised": False, "count": 0, "prefix_sent": prefix,
                "error": f"Error de red al consultar Pwned Passwords: {exc}"}

    if r.status_code != 200:
        return {"compromised": False, "count": 0, "prefix_sent": prefix,
                "error": f"API Pwned Passwords devolvió HTTP {r.status_code}."}

    for line in r.text.splitlines():
        if ":" not in line:
            continue
        h_suf, count_str = line.strip().split(":", 1)
        if h_suf.upper() == suffix:
            count = int(count_str) if count_str.isdigit() else 0
            return {
                "compromised": count > 0,
                "count": count,
                "prefix_sent": prefix,
                "error": None,
            }

    return {"compromised": False, "count": 0, "prefix_sent": prefix, "error": None}


# Para qué sirve: da formato legible de secciones al resultado de brechas para renderizar HTML y PDF.
# Cómo funciona: agrupa resumen de estado/riesgo, listado de brechas con sus datos expuestos y recomendaciones.
def format_breach_report(res: dict) -> dict:
    """Convierte el resultado de `check_email_breaches` en secciones legibles para HTML y PDF."""
    if not res:
        return {}

    estado = (
        f"[BAJA] EXPUESTO en {res['breach_count']} brecha(s)"
        if res.get("breached")
        else "[ALTA] Sin brechas detectadas en fuentes consultadas"
    )
    resumen = [
        f"Correo analizado: {res.get('email', '—')}",
        f"Estado: {estado}",
        f"¿Contraseñas/hashes en brechas?: {'SÍ (cambiar de inmediato)' if res.get('passwords_exposed') else 'No indicado'}",
        f"¿Teléfono expuesto en brechas?: {'SÍ' if res.get('phone_exposed') else 'No indicado'}",
        f"Fuentes consultadas: {', '.join(res.get('sources_checked') or []) or 'ninguna (error de red)'}",
    ]
    if res.get("risk_label"):
        resumen.append(f"Nivel de riesgo (XposedOrNot): {res['risk_label']} (score: {res.get('risk_score')})")
    if res.get("exposed_data_classes"):
        resumen.append(f"Tipos de datos expuestos: {', '.join(res['exposed_data_classes'])}")
    if res.get("errors"):
        resumen.append(f"Avisos / errores: {'; '.join(res['errors'])}")

    detalle = [
        f"{b['name']} ({b.get('date', '—')}) [{', '.join(b.get('sources', []))}]"
        + (f" — Datos: {', '.join(b['data_classes'])}" if b.get("data_classes") else "")
        for b in res.get("breaches") or []
    ]

    secciones = {
        "Exposición en brechas de datos": resumen,
        "Brechas identificadas": detalle or ["(ninguna brecha registrada)"],
        "Recomendaciones defensivas": res.get("recommendations") or [],
    }
    return secciones


# Para qué sirve: transforma las brechas encontradas en filas planas listas para exportar a CSV.
# Cómo funciona: aplana listas (`sources` con `+`, `data_classes` con `; `) en un diccionario por brecha.
def breaches_csv_rows(res: dict) -> list:
    """Genera filas planas para exportar `brechas_detectadas.csv`."""
    email = res.get("email", "")
    return [
        {
            "email": email,
            "brecha": b.get("name", ""),
            "dominio": b.get("domain", ""),
            "fecha": b.get("date", ""),
            "verificada": "si" if b.get("verified") else "no",
            "fuentes": "+".join(b.get("sources") or []),
            "datos_expuestos": "; ".join(b.get("data_classes") or []),
        }
        for b in res.get("breaches") or []
    ]
