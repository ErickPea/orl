# -*- coding: utf-8 -*-
"""
Investigación por correo electrónico:
  1. Validación de sintaxis y registros DNS MX (`dnspython`) para descartar dominios inválidos.
  2. Consulta pública a Gravatar (perfil, nombre, ubicación y cuentas/username vinculados).
  3. Verificación con Holehe diferenciando servicios registrados [+], no usados [-] y limitados [x].
  4. Derivación de usernames (combinando correo + Gravatar) y consolidación con scoring de confianza.
"""
import hashlib
import re
from pathlib import Path
from . import utils
from . import reports
from .username_search import run_username_search

# Importaciones opcionales para validación MX y consultas HTTP (Gravatar)
try:
    import dns.resolver
    DNS_OK = True
except ImportError:
    DNS_OK = False

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

PROVIDERS = ["gmail.com", "hotmail.com", "outlook.com", "yahoo.com",
             "icloud.com", "protonmail.com", "live.com", "aol.com"]
PATTERNS = ["{u}@{d}", "{u}1@{d}", "{u}123@{d}", "{u}.{u}@{d}", "{u}_@{d}"]

# Tags excluidos por defecto al derivar usernames desde correo
EXCLUDES_EMAIL = ["porn", "dating", "adult", "gambling", "crypto"]

# Validación básica RFC de sintaxis de correo
_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# Parseo mejorado de Holehe: captura estado ([+], [-], [x]), dominio y texto extra opcional
# (antes el regex exigía fin de línea justo tras el dominio y perdía hits con info de recuperación)
_LINEA_HOLEHE = re.compile(r"^\[([+\-x])\]\s+([\w.-]+\.[a-z]{2,})(?:\s+(.*))?$", re.I)


# Para qué sirve: descarta correos con errores de escritura o dominios sin servidor de correo antes de consultar APIs.
# Cómo funciona: valida formato con regex RFC y consulta registros DNS MX por prioridad (con fallback a registro A).
def validate_email_domain(email: str) -> dict:
    """
    Valida la sintaxis del correo y consulta los registros DNS MX del dominio
    (con respaldo en registro A si el dominio no define MX).
    Evita perder tiempo en dominios inexistentes o mal escritos.
    """
    email_limpio = (email or "").strip().lower()
    valido = bool(_EMAIL_RE.match(email_limpio))
    dominio = email_limpio.split("@", 1)[1] if "@" in email_limpio else ""
    res = {
        "email": email_limpio,
        "valid_syntax": valido,
        "domain": dominio,
        "has_mx": None,
        "mx_records": [],
        "note": "",
    }
    if not valido or not dominio:
        res["note"] = "Formato de correo inválido."
        return res

    if not DNS_OK:
        res["note"] = "dnspython no instalado; se omitió verificación MX."
        return res

    try:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = 5.0
        resp = resolver.resolve(dominio, "MX")
        registros = sorted(
            (r.preference, str(r.exchange).rstrip(".")) for r in resp
        )
        res["has_mx"] = bool(registros)
        res["mx_records"] = [host for _, host in registros[:5]]
        res["note"] = f"Dominio activo ({len(registros)} registros MX)."
    except dns.resolver.NXDOMAIN:
        res["has_mx"] = False
        res["note"] = "El dominio no existe (NXDOMAIN)."
    except dns.resolver.NoAnswer:
        # Algunos dominios reciben correo por registro A sin MX explícito
        try:
            dns.resolver.resolve(dominio, "A", lifetime=4.0)
            res["has_mx"] = True
            res["note"] = "Sin MX explícito, pero el dominio tiene registro A."
        except Exception:  # noqa: BLE001
            res["has_mx"] = False
            res["note"] = "El dominio no tiene registros MX ni A."
    except Exception as exc:  # noqa: BLE001
        res["has_mx"] = None
        res["note"] = f"No se pudo verificar DNS ({exc.__class__.__name__})."
    return res


# Para qué sirve: enriquece el correo buscando un perfil público de Gravatar (nombre, alias, ubicación y cuentas).
# Cómo funciona: calcula el hash MD5 del correo en minúsculas y consulta `gravatar.com/{md5}.json` capturando errores sin cortar el flujo.
def lookup_gravatar(email: str, timeout: int = 8) -> dict:
    """
    Consulta el perfil público de Gravatar usando el hash MD5 del correo en minúsculas.
    Si el usuario tiene perfil público en Gravatar, suele revelar nombre visible,
    username preferido, ubicación, bio y cuentas enlazadas.
    """
    email_limpio = (email or "").strip().lower()
    md5_hash = hashlib.md5(email_limpio.encode("utf-8")).hexdigest()  # noqa: S324 - requerido por API pública de Gravatar
    sha256_hash = hashlib.sha256(email_limpio.encode("utf-8")).hexdigest()
    res = {
        "found": False,
        "md5": md5_hash,
        "sha256": sha256_hash,
        "profile_url": None,
        "display_name": None,
        "preferred_username": None,
        "location": None,
        "about": None,
        "accounts": [],
    }
    if not REQUESTS_OK or not email_limpio:
        return res

    url = f"https://www.gravatar.com/{md5_hash}.json"
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "OSINT-Auto/2.0"})
        if r.status_code != 200:
            return res
        data = r.json()
        entry = (data.get("entry") or [{}])[0]
        res["found"] = True
        res["profile_url"] = entry.get("profileUrl")
        res["display_name"] = entry.get("displayName")
        res["preferred_username"] = entry.get("preferredUsername")
        res["location"] = entry.get("currentLocation")
        res["about"] = entry.get("aboutMe")
        cuentas = []
        for acc in entry.get("accounts") or []:
            u = acc.get("url")
            dom = acc.get("shortname") or acc.get("domain") or "cuenta"
            if u:
                cuentas.append(f"{dom}: {u}")
        res["accounts"] = cuentas
    except Exception:  # noqa: BLE001 - Gravatar es enriquecimiento opcional; no debe romper el flujo
        pass
    return res


# Para qué sirve: genera correos candidatos a partir de un username para probarlos con Holehe.
# Cómo funciona: limpia caracteres no válidos y combina `PROVIDERS` x `PATTERNS` quitando repetidos con `dict.fromkeys`.
def generate_emails(username: str) -> list:
    """Recibe un username y devuelve la lista de correos candidatos sin repetidos."""
    u = re.sub(r"[^a-zA-Z0-9._-]", "", username.lower())
    if not u:
        return []
    candidatos = [p.format(u=u, d=d) for d in PROVIDERS for p in PATTERNS]
    return list(dict.fromkeys(candidatos))  # dict conserva el orden y quita duplicados


# Para qué sirve: interpreta la salida de consola de Holehe sin mezclar cuentas inexistentes con bloqueos por rate-limit.
# Cómo funciona: aplica `_LINEA_HOLEHE` por línea clasificando `[+]` (registrado + pista), `[-]` (no usado) y `[x]` (limitado).
def parse_holehe_output(text: str) -> dict:
    """
    Parsea la salida de Holehe separando:
      - registered:         dominios con [+] (confirmados)
      - registered_details: dominios + detalle extra si Holehe mostró pistas de recuperación
      - rate_limited:       dominios con [x] (bloqueados temporalmente; NO asumir que no existen)
      - not_used_count:     cantidad de dominios con [-]
    """
    registered, details, rate_limited = [], [], []
    not_used = 0
    for raw in (text or "").splitlines():
        linea = raw.strip()
        # Ignora la leyenda y el resumen final de Holehe
        if "Email used" in linea or "websites checked" in linea:
            continue
        m = _LINEA_HOLEHE.match(linea)
        if not m:
            continue
        estado, dominio, extra = m.group(1), m.group(2), (m.group(3) or "").strip()
        if estado == "+":
            registered.append(dominio)
            details.append(f"{dominio} ({extra})" if extra else dominio)
        elif estado == "-":
            not_used += 1
        elif estado.lower() == "x":
            rate_limited.append(dominio)

    return {
        "registered": list(dict.fromkeys(registered)),
        "registered_details": list(dict.fromkeys(details)),
        "rate_limited": list(dict.fromkeys(rate_limited)),
        "not_used_count": not_used,
    }


# Para qué sirve: verifica en qué servicios web está registrado un correo usando la herramienta Holehe.
# Cómo funciona: ejecuta `holehe <email> --no-color` (sin `--only-used` para ver `[x]`), guarda el log y parsea stdout.
def run_holehe(email: str, out_dir: Path) -> dict:
    """
    Corre Holehe sobre un correo SIN `--only-used` para poder detectar también
    qué servicios respondieron con rate-limit `[x]` y no confundirlos con no registrados.
    """
    base = utils.tool_command("holehe")
    if not base:
        return {"error": "holehe no instalado", "email": email,
                "registered": [], "registered_details": [], "rate_limited": [], "not_used_count": 0}

    log = out_dir / f"holehe_{email.replace('@', '_at_')}.txt"
    rc, out, e = utils.run_command(base + [email, "--no-color"], timeout=180)
    log.write_text(out + "\n" + e, encoding="utf-8")

    parsed = parse_holehe_output(out)
    return {
        "email": email,
        "rc": rc,
        "registered": parsed["registered"],
        "registered_details": parsed["registered_details"],
        "rate_limited": parsed["rate_limited"],
        "not_used_count": parsed["not_used_count"],
        "log": str(log),
    }


# Para qué sirve: busca cuentas de correo activas derivadas de un username hasta un límite dado.
# Cómo funciona: toma los primeros `limit` candidatos de `generate_emails`, ejecuta `run_holehe` y retorna solo los positivos.
def run_email_search(username: str, out_dir: Path, limit: int = 12) -> dict:
    """Verifica con Holehe los primeros `limit` candidatos; devuelve {email: [servicios]} solo de los registrados."""
    if not utils.tool_available("holehe"):
        utils.warn("Holehe no instalado; se omite la verificación de correos.")
        return {}

    candidatos = generate_emails(username)[:limit]
    utils.info(f"Verificando {len(candidatos)} correos con Holehe...")
    encontrados = {}
    for i, email in enumerate(candidatos, 1):
        utils.info(f"  [{i}/{len(candidatos)}] {email}")
        r = run_holehe(email, out_dir)
        if r.get("registered"):
            encontrados[email] = r["registered_details"] or r["registered"]
            msg_rl = f" ({len(r['rate_limited'])} con rate-limit)" if r.get("rate_limited") else ""
            utils.ok(f"      registrado en {len(r['registered'])} servicios{msg_rl}")
    return encontrados


# Para qué sirve: ejecuta la investigación directa de un correo electrónico ya conocido.
# Cómo funciona: encadena validación DNS/MX (`validate_email_domain`), perfil público (`lookup_gravatar`) y registro en servicios (`run_holehe`).
def run_email_lookup(email: str, out_dir: Path) -> dict:
    """
    Investiga directamente un correo YA CONOCIDO:
      1) Validación sintaxis + MX
      2) Gravatar público
      3) Holehe (registrados + rate-limited)
    """
    mx = validate_email_domain(email)
    if mx["valid_syntax"]:
        if mx["has_mx"] is False:
            utils.warn(f"Validación DNS: {mx['note']} (puede que el correo no exista).")
        elif mx["has_mx"] is True:
            utils.ok(f"Validación DNS: {mx['note']} -> {', '.join(mx['mx_records'][:3]) or 'A'}")
        else:
            utils.info(f"Validación DNS: {mx['note']}")
    else:
        utils.err(mx["note"])
        return {"error": mx["note"], "email": email, "mx": mx, "gravatar": {"found": False},
                "registered": [], "registered_details": [], "rate_limited": []}

    gravatar = lookup_gravatar(email)
    if gravatar["found"]:
        utils.ok(f"Gravatar público encontrado: {gravatar.get('display_name') or gravatar.get('profile_url')}")
    else:
        utils.info("Gravatar: sin perfil público asociado.")

    if not utils.tool_available("holehe"):
        utils.warn("Holehe no instalado; se devuelven solo DNS/MX y Gravatar.")
        return {"error": "holehe no instalado", "email": email, "mx": mx, "gravatar": gravatar,
                "registered": [], "registered_details": [], "rate_limited": []}

    utils.info(f"Verificando correo '{email}' con Holehe...")
    r = run_holehe(email, out_dir)
    r["mx"] = mx
    r["gravatar"] = gravatar

    if r.get("registered"):
        utils.ok(f"Registrado en {len(r['registered'])} servicios:")
        for s in r["registered_details"]:
            print(f"     + {s}")
    else:
        utils.warn("No se detectaron registros confirmados para ese correo.")
    if r.get("rate_limited"):
        utils.warn(f"Servicios con rate-limit (no concluyentes): {len(r['rate_limited'])}")
    return r


# Para qué sirve: amplía la búsqueda de un correo hacia posibles perfiles sociales asociados al mismo alias.
# Cómo funciona: corre `run_email_lookup`, deriva usernames del prefijo del correo y Gravatar (`3..30` chars) y los pasa por Maigret/Sherlock + consolidación.
def run_email_osint(email: str, out_dir: Path) -> dict:
    """
    Pipeline ampliado para un correo conocido:
      1. Validación MX + Gravatar + Holehe (servicios registrados y limitados)
      2. Deriva posibles usernames del correo (más `preferredUsername` de Gravatar si existe)
         y los pasa por Maigret/Sherlock consolidando URLs reales con nivel de confianza.
    """
    lookup = run_email_lookup(email, out_dir)
    result = {
        "email": email,
        "mx": lookup.get("mx", {}),
        "gravatar": lookup.get("gravatar", {}),
        "services": lookup.get("registered_details") or lookup.get("registered", []),
        "rate_limited": lookup.get("rate_limited", []),
        "usernames_tested": [],
        "profiles_found": {},
    }

    # --- Derivar usernames del correo y de Gravatar ---
    local_part = email.split("@")[0]
    candidates_set = {
        local_part,
        local_part.replace(".", ""),
        local_part.replace(".", "_"),
        local_part.replace("_", "."),
    }
    grav_user = (result["gravatar"] or {}).get("preferred_username")
    if grav_user:
        candidates_set.add(grav_user)

    candidates = [c for c in sorted(candidates_set) if 3 <= len(c) <= 30]
    result["usernames_tested"] = candidates

    if not candidates:
        return result

    if not (utils.tool_available("maigret") or utils.tool_available("sherlock")):
        utils.warn("Ni Maigret ni Sherlock disponibles; se omite búsqueda de usernames derivados.")
        return result

    utils.info(f"Probando usernames derivados del correo: {candidates}")
    for u in candidates:
        sub_dir = out_dir / f"derived_{u}"
        sub_dir.mkdir(exist_ok=True)
        utils.info(f"  -> username candidato: {u}")
        try:
            run_username_search(u, sub_dir, top=100, exclude=EXCLUDES_EMAIL)
        except Exception as e:  # noqa: BLE001
            utils.warn(f"    fallo al buscar '{u}': {e}")
            continue
        # En vez de listar nombres de archivos crudos, consolidamos perfiles reales con confianza
        perfiles = reports.consolidate_profiles(sub_dir, extra_excludes=EXCLUDES_EMAIL)
        if perfiles:
            result["profiles_found"][u] = [
                f"[{p['confianza']}] {p['sitio']} ({p['fuentes']}): {p['url']}"
                for p in perfiles
            ]

    return result
