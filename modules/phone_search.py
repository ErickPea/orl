# -*- coding: utf-8 -*-
"""
Análisis técnico de números de teléfono (con `phonenumbers`) y verificación
de cuentas asociadas con Ignorant.
"""
import re
from pathlib import Path
from urllib.parse import quote_plus
from . import utils

# Importación opcional de phonenumbers (librería oficial de Google para validar/enriquecer teléfonos)
try:
    import phonenumbers
    from phonenumbers import carrier, geocoder, timezone, PhoneNumberType
    PHONENUMBERS_OK = True
except ImportError:
    PHONENUMBERS_OK = False

# Prefijos de respaldo por si `phonenumbers` no está instalado
PREFIJOS = {
    "57": "co", "52": "mx", "34": "es", "1": "us", "54": "ar",
    "56": "cl", "51": "pe", "58": "ve", "593": "ec", "598": "uy",
}

# Mapa legible en español de los tipos de línea que devuelve phonenumbers
_TIPOS_LINEA = {
    0: "Fijo",
    1: "Móvil",
    2: "Fijo o Móvil",
    3: "Gratuito (Toll-Free)",
    4: "Tarifa Premium",
    5: "Costo Compartido",
    6: "VoIP",
    7: "Número Personal",
    8: "Localizador (Pager)",
    9: "UAN",
    10: "Buzón de Voz",
    99: "Desconocido",
}

# Patrón para parsear líneas de salida de Ignorant: "[+] instagram.com", "[-] amazon.com", "[x] snapchat.com"
_LINEA_IGNORANT = re.compile(r"^\[([+\-x])\]\s+([\w.-]+\.[a-z]{2,})(?:\s+(.*))?$", re.I)


# Para qué sirve: divide un número telefónico internacional en código de país y número nacional.
# Cómo funciona: usa `phonenumbers.parse` (anteponiendo `+` si falta) y recurre a `PREFIJOS` si la librería no está disponible.
def split_phone(phone: str):
    """
    Separa '+573001234567' en ('57', '3001234567').
    Usa `phonenumbers` si está disponible (soporta todos los países del mundo)
    y cae al diccionario `PREFIJOS` como respaldo.
    """
    if PHONENUMBERS_OK:
        try:
            # Si el usuario olvidó el '+', intentamos anteponerlo para formato internacional
            candidato = phone.strip()
            if candidato and not candidato.startswith("+"):
                candidato = "+" + re.sub(r"\D", "", candidato)
            parsed = phonenumbers.parse(candidato, None)
            if parsed.country_code and parsed.national_number:
                return str(parsed.country_code), str(parsed.national_number)
        except Exception:  # noqa: BLE001
            pass

    digitos = re.sub(r"\D", "", phone)
    for p in sorted(PREFIJOS, key=len, reverse=True):
        if digitos.startswith(p):
            return p, digitos[len(p):]
    return None, digitos


_VOIP_CARRIER_HINTS = (
    "voip", "google voice", "skype", "bandwidth", "twilio", "vonage",
    "textnow", "dingtone", "zadarma", "sip", "virtual", "ringcentral",
)


# Para qué sirve: produce formatos de marcación, evalúa si la línea es VoIP y arma dorks/enlaces directos (WhatsApp/Telegram).
# Cómo funciona: combina formatos E.164/internacional/nacional/segmentados, inspecciona operador/tipo y arma URLs de búsqueda.
def build_phone_variants_and_dorks(phone: str, analysis: dict) -> dict:
    """
    Genera variantes de formato habituales (E.164, internacional, nacional, con guiones/espacios),
    evaluación de línea virtual/VoIP y consultas OSINT listas para buscadores públicos.
    """
    e164 = analysis.get("e164") or ""
    cc = str(analysis.get("country_code") or "")
    nat = str(analysis.get("national_number") or "")
    digits_full = re.sub(r"\D", "", e164 or phone)
    variants = []

    # Para qué sirve: agrega una variante de formato a la lista evitando vacíos y duplicados.
    # Cómo funciona: limpia espacios y comprueba pertenencia en `variants`.
    def _add(v: str):
        v = (v or "").strip()
        if v and v not in variants:
            variants.append(v)

    _add(e164)
    if PHONENUMBERS_OK and e164:
        try:
            parsed = phonenumbers.parse(e164, None)
            _add(phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL))
            _add(phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL))
        except Exception:
            pass
    if cc and nat:
        _add(f"+{cc} {nat}")
        _add(f"+{cc}{nat}")
    _add(nat)
    if len(nat) == 10:
        _add(f"{nat[:3]} {nat[3:6]} {nat[6:]}")
        _add(f"{nat[:3]}-{nat[3:6]}-{nat[6:]}")
        if cc:
            _add(f"+{cc} {nat[:3]} {nat[3:6]} {nat[6:]}")
    elif len(nat) == 9:
        _add(f"{nat[:3]} {nat[3:6]} {nat[6:]}")
        _add(f"{nat[:3]}-{nat[3:6]}-{nat[6:]}")

    carrier_low = (analysis.get("carrier") or "").lower()
    line_type = (analysis.get("line_type") or "").strip()
    is_voip = (line_type.lower() == "voip") or any(h in carrier_low for h in _VOIP_CARRIER_HINTS)
    if is_voip:
        risk_note = "Línea VoIP / virtual detectada (frecuente en números temporales o servicios en la nube)."
    elif line_type == "Móvil":
        risk_note = "Línea móvil estándar. El operador refleja el bloque original (puede variar por portabilidad)."
    elif line_type:
        risk_note = f"Tipo de línea clasificado como {line_type}."
    else:
        risk_note = "No se pudo determinar el tipo de línea."

    quoted = [f'"{v}"' for v in variants[:4] if v]
    or_expr = " OR ".join(quoted) if quoted else f'"{phone}"'
    q_general = f"({or_expr})"
    q_social = f"({or_expr}) (site:facebook.com OR site:instagram.com OR site:linkedin.com OR site:x.com)"
    q_docs = f"({or_expr}) (filetype:pdf OR filetype:doc OR filetype:docx OR site:scribd.com)"
    q_contact = f'({or_expr}) ("whatsapp" OR "contacto" OR "tel" OR "cel")'

    # Para qué sirve: construye enlaces de búsqueda directa en Google, DuckDuckGo y Bing para un dork dado.
    # Cómo funciona: codifica la consulta con `quote_plus` e interpola la URL de cada buscador.
    def _links(q: str) -> dict:
        eq = quote_plus(q)
        return {
            "google": f"https://www.google.com/search?q={eq}",
            "duckduckgo": f"https://duckduckgo.com/?q={eq}",
            "bing": f"https://www.bing.com/search?q={eq}",
        }

    dorks = [
        {"label": "Menciones exactas en la web", "query": q_general, **_links(q_general)},
        {"label": "Redes sociales abiertas", "query": q_social, **_links(q_social)},
        {"label": "Documentos públicos (PDF / CV)", "query": q_docs, **_links(q_docs)},
        {"label": "Directorios y páginas de contacto", "query": q_contact, **_links(q_contact)},
    ]

    direct_links = {}
    if digits_full:
        direct_links["whatsapp"] = f"https://wa.me/{digits_full}"
        direct_links["telegram"] = f"https://t.me/+{digits_full}"

    return {
        "variants": variants,
        "voip_assessment": {"is_voip": is_voip, "note": risk_note},
        "dorks": dorks,
        "direct_links": direct_links,
    }


# Para qué sirve: obtiene metadatos técnicos locales del número (país, operador, tipo de línea y huso horario) sin red.
# Cómo funciona: parsea con `phonenumbers` consultando `geocoder`, `carrier`, `number_type` y `timezone`, con fallback si falta la librería.
def analyze_phone(phone: str) -> dict:
    """
    Realiza análisis técnico local del número usando `phonenumbers`:
    formato E.164, validez, país/región, operador (carrier), tipo de línea y zona horaria.
    No requiere conexión a internet ni API externa.
    """
    info = {
        "input": phone,
        "e164": None,
        "country_code": None,
        "national_number": None,
        "valid": None,
        "possible": None,
        "region_code": None,
        "location": None,
        "carrier": None,
        "line_type": None,
        "timezones": [],
    }

    if not PHONENUMBERS_OK:
        codigo, numero = split_phone(phone)
        info["country_code"] = codigo
        info["national_number"] = numero
        info["e164"] = f"+{codigo}{numero}" if codigo else None
        info["note"] = "Instala `phonenumbers` para obtener operador, tipo de línea y validación internacional."
        return info

    candidato = phone.strip()
    if candidato and not candidato.startswith("+"):
        candidato = "+" + re.sub(r"\D", "", candidato)

    try:
        parsed = phonenumbers.parse(candidato, None)
    except phonenumbers.NumberParseException as exc:
        info["error"] = f"No se pudo interpretar el número: {exc}"
        return info

    info["country_code"] = str(parsed.country_code)
    info["national_number"] = str(parsed.national_number)
    info["e164"] = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    info["possible"] = phonenumbers.is_possible_number(parsed)
    info["valid"] = phonenumbers.is_valid_number(parsed)
    info["region_code"] = phonenumbers.region_code_for_number(parsed)
    info["location"] = geocoder.description_for_number(parsed, "es") or None
    info["carrier"] = carrier.name_for_number(parsed, "es") or None
    tipo = phonenumbers.number_type(parsed)
    info["line_type"] = _TIPOS_LINEA.get(int(tipo), "Desconocido")
    info["timezones"] = list(timezone.time_zones_for_number(parsed))
    return info


# Para qué sirve: interpreta la salida de Ignorant distinguiendo registros confirmados de servicios limitados por rate-limit.
# Cómo funciona: filtra banners y aplica `_LINEA_IGNORANT` para agrupar `[+]`, `[-]` y `[x]` sin duplicados.
def parse_ignorant_output(text: str) -> dict:
    """
    Extrae resultados estructurados de la salida de Ignorant, separando:
      - registered:   servicios con [+] (cuenta encontrada)
      - not_used:     servicios con [-] (no registrada)
      - rate_limited: servicios con [x] (bloqueo temporal / rate limit)
    Ignora la leyenda de ayuda y los banners ASCII.
    """
    registered, not_used, rate_limited = [], [], []
    for raw in (text or "").splitlines():
        linea = raw.strip()
        # Ignoramos la línea de leyenda que siempre imprime Ignorant
        if "Phone number used" in linea or "websites checked" in linea:
            continue
        m = _LINEA_IGNORANT.match(linea)
        if not m:
            continue
        estado, dominio, extra = m.group(1), m.group(2), (m.group(3) or "").strip()
        etiqueta = f"{dominio} ({extra})" if extra else dominio
        if estado == "+":
            registered.append(etiqueta)
        elif estado == "-":
            not_used.append(dominio)
        elif estado.lower() == "x":
            rate_limited.append(dominio)

    return {
        "registered": list(dict.fromkeys(registered)),
        "not_used": list(dict.fromkeys(not_used)),
        "rate_limited": list(dict.fromkeys(rate_limited)),
    }


# Para qué sirve: ejecuta el flujo completo de OSINT telefónico combinando análisis local, dorks e Ignorant.
# Cómo funciona: valida y normaliza con `analyze_phone`, genera variantes/dorks, lanza `ignorant <país> <número>` y guarda el log.
def run_phone_search(phone: str, out_dir: Path) -> dict:
    """
    Ejecuta análisis técnico (`phonenumbers`) y luego Ignorant (`ignorant <país> <número>`).
    Guarda el log crudo en out_dir y devuelve resultados estructurados para reportes.
    """
    # 1) Análisis técnico local (siempre aporta precisión, incluso si Ignorant falla o limita)
    analysis = analyze_phone(phone)
    if analysis.get("error"):
        utils.err(analysis["error"])
        return {"phone": phone, "analysis": analysis, "error": analysis["error"],
                "registered": [], "not_used": [], "rate_limited": []}

    if analysis.get("e164"):
        utils.ok(
            f"Teléfono normalizado: {analysis['e164']} | "
            f"Válido: {'Sí' if analysis.get('valid') else 'No/Revisar'} | "
            f"País: {analysis.get('location') or analysis.get('region_code') or '?'} | "
            f"Operador: {analysis.get('carrier') or 'no determinado'} | "
            f"Tipo: {analysis.get('line_type') or '?'}"
        )

    codigo = analysis.get("country_code")
    numero = analysis.get("national_number")
    if not codigo or not numero:
        codigo, numero = split_phone(phone)

    if not codigo:
        utils.err("No reconocí el código de país. Usa formato internacional, ej: +573001234567")
        return {"phone": phone, "analysis": analysis, "error": "prefijo no reconocido",
                "registered": [], "not_used": [], "rate_limited": []}

    extras = build_phone_variants_and_dorks(phone, analysis)

    # 2) Verificación en servicios con Ignorant (si está instalado)
    base = utils.tool_command("ignorant")
    if not base:
        utils.warn("Ignorant no instalado; se devuelve solo el análisis técnico del número.")
        return {
            "phone": analysis.get("e164") or phone,
            "analysis": analysis,
            **extras,
            "registered": [],
            "not_used": [],
            "rate_limited": [],
            "warning": "ignorant no instalado",
        }

    utils.info(f"Ejecutando Ignorant para +{codigo} {numero}...")
    rc, out, e = utils.run_command(base + [codigo, numero], timeout=300)
    log = out_dir / f"ignorant_{codigo}{numero}.txt"
    log.write_text(out + "\n" + e, encoding="utf-8")

    parsed = parse_ignorant_output(out)
    if rc == 0:
        utils.ok(
            f"Ignorant terminó: {len(parsed['registered'])} registrados, "
            f"{len(parsed['not_used'])} no usados, {len(parsed['rate_limited'])} limitados (rate-limit)."
        )
    else:
        utils.err(f"Ignorant rc={rc}: {e[:200]}")

    return {
        "phone": analysis.get("e164") or phone,
        "analysis": analysis,
        **extras,
        "rc": rc,
        "registered": parsed["registered"],
        "not_used": parsed["not_used"],
        "rate_limited": parsed["rate_limited"],
        "stdout": out,
        "log": str(log),
    }
