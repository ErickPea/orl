# -*- coding: utf-8 -*-
"""
Enfoque en Redes Sociales, metadatos públicos abiertos, grafo de huella digital
y evaluación defensiva de exposición/privacidad.

Principios de diseño:
  - Solo lee información pública accesible SIN autenticación (APIs públicas oficiales
    como GitHub/Reddit y etiquetas estándar Open Graph / HTML <meta>).
  - NUNCA intenta evadir muros de inicio de sesión, CAPTCHAs ni controles anti-scraping.
  - Cuando una plataforma protege los detalles detrás de login (ej. Facebook, Instagram,
    LinkedIn, X), lo indica explícitamente como `login_wall` para revisión manual autorizada.
"""
import re
from html.parser import HTMLParser
from urllib.parse import urlparse

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

_USER_AGENT = "OSINT-Auto/2.0 (public-metadata-inspector)"

# Palabras clave de plataformas sociales y comunidades públicas
SOCIAL_KEYWORDS = (
    "facebook", "instagram", "twitter", "x.com", "tiktok", "linkedin",
    "youtube", "youtu.be", "snapchat", "telegram", "t.me", "pinterest",
    "reddit", "threads", "twitch", "github", "gitlab", "medium", "dev.to",
    "vimeo", "soundcloud", "spotify", "behance", "dribbble", "mastodon",
    "bluesky", "bsky.app", "tumblr", "flickr", "vk.com",
)

# Dominios conocidos que exigen inicio de sesión para ver secciones del perfil;
# no intentamos rasparlos automáticamente y los marcamos directo para revisión manual.
LOGIN_WALL_DOMAINS = {
    "facebook.com", "m.facebook.com", "fb.com",
    "instagram.com",
    "linkedin.com",
    "x.com", "twitter.com",
    "threads.net",
}

# Rutas típicas de redirección a muro de inicio de sesión
_LOGIN_PATH_MARKERS = ("/login", "/accounts/login", "/authwall", "/checkpoint", "/signin", "/sign_in")


class _MetaParser(HTMLParser):
    """Extractor ligero de <title> y etiquetas <meta> (Open Graph / description) sin dependencias extra."""

    # Para qué sirve: inicializa los acumuladores del parser HTML para título y etiquetas meta.
    # Cómo funciona: prepara `self.meta`, `self.in_title` y `self.title_parts` llamando a `super().__init__()`.
    def __init__(self):
        super().__init__()
        self.meta = {}
        self.in_title = False
        self.title_parts = []

    # Para qué sirve: captura etiquetas `<title>` y `<meta>` relevantes durante el parseo de HTML público.
    # Cómo funciona: activa `in_title` al abrir `<title>` y guarda claves Open Graph / Twitter / `description` en `self.meta`.
    def handle_starttag(self, tag, attrs):
        tag_l = tag.lower()
        if tag_l == "title":
            self.in_title = True
            return
        if tag_l != "meta":
            return
        d = {k.lower(): (v or "").strip() for k, v in attrs}
        content = d.get("content", "")
        if not content:
            return
        key = (d.get("property") or d.get("name") or "").lower()
        if key in ("og:title", "og:description", "og:image", "og:site_name", "description", "twitter:title", "twitter:description"):
            self.meta.setdefault(key, content[:280])

    # Para qué sirve: cierra la captura de texto del título al encontrar `</title>`.
    # Cómo funciona: desactiva `self.in_title`.
    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self.in_title = False

    # Para qué sirve: acumula los fragmentos de texto dentro de `<title>...</title>`.
    # Cómo funciona: agrega `data` a `self.title_parts` mientras `self.in_title` sea verdadero.
    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)

    # Para qué sirve: devuelve el título de la página limpio y acotado a 180 caracteres.
    # Cómo funciona: une `title_parts`, colapsa espacios y recorta la cadena.
    @property
    def title(self) -> str:
        return " ".join("".join(self.title_parts).split())[:180]


# Para qué sirve: extrae el dominio normalizado sin prefijo `www.` desde una URL.
# Cómo funciona: parsea `netloc` en minúsculas con `urlparse` y remueve `"www."`.
def _clean_host(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


# Para qué sirve: determina si un hallazgo corresponde a una red social o comunidad pública.
# Cómo funciona: busca alguna palabra clave de `SOCIAL_KEYWORDS` dentro de `sitio` y `url`.
def is_social_profile(sitio: str, url: str) -> bool:
    """Indica si el perfil corresponde a una red social o comunidad pública."""
    texto = f"{sitio} {url}".lower()
    return any(k in texto for k in SOCIAL_KEYWORDS)


# Para qué sirve: obtiene el username limpio desde la ruta de una URL de perfil social.
# Cómo funciona: toma el último segmento no vacío (o el posterior a `/u/` o `/user/` en Reddit) y sanea caracteres.
def _extract_username_from_url(url: str) -> str:
    """Extrae el último segmento útil del path de una URL de perfil (ej. github.com/user -> user)."""
    partes = [p for p in urlparse(url or "").path.split("/") if p]
    if not partes:
        return ""
    ultimo = partes[-1].lstrip("@")
    # En Reddit (/user/nombre o /u/nombre)
    if len(partes) >= 2 and partes[-2].lower() in ("user", "u"):
        return partes[-1]
    return re.sub(r"[^a-zA-Z0-9._\-]", "", ultimo)


# Para qué sirve: consulta metadatos públicos oficiales de un usuario en GitHub sin autenticación.
# Cómo funciona: llama a `api.github.com/users/{username}`, controla 404/403 y extrae bio, ubicación, org y contadores.
def fetch_github_public(username: str, timeout: int = 8) -> dict:
    """Consulta la API pública oficial de GitHub (sin autenticación) para un usuario."""
    if not REQUESTS_OK or not username:
        return {"status": "unavailable", "note": "requests no disponible"}
    try:
        r = requests.get(
            f"https://api.github.com/users/{username}",
            headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        if r.status_code == 404:
            return {"status": "not_found", "note": "Perfil no encontrado en GitHub API."}
        if r.status_code == 403:
            return {"status": "rate_limited", "note": "Límite temporal de API pública de GitHub."}
        if r.status_code != 200:
            return {"status": "unavailable", "note": f"GitHub API devolvió HTTP {r.status_code}."}
        d = r.json()
        detalles = {}
        for k_out, k_in in (
            ("Nombre público", "name"),
            ("Bio", "bio"),
            ("Empresa / Org", "company"),
            ("Ubicación", "location"),
            ("Sitio web", "blog"),
            ("Repositorios públicos", "public_repos"),
            ("Seguidores", "followers"),
            ("Cuenta creada", "created_at"),
        ):
            val = d.get(k_in)
            if val not in (None, "", 0):
                detalles[k_out] = str(val)[:200]
        return {
            "status": "public_ok",
            "title": d.get("name") or d.get("login"),
            "description": d.get("bio") or "",
            "avatar": d.get("avatar_url") or "",
            "details": detalles,
            "note": "Metadatos obtenidos desde la API pública oficial de GitHub.",
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "note": f"Error de red: {exc.__class__.__name__}"}


# Para qué sirve: obtiene información pública básica de un usuario de Reddit sin OAuth2.
# Cómo funciona: consulta `/user/{username}/about.json`, maneja 404/403/429 y extrae título, descripción y karma.
def fetch_reddit_public(username: str, timeout: int = 8) -> dict:
    """Consulta el endpoint JSON público oficial de Reddit para metadatos básicos del perfil."""
    if not REQUESTS_OK or not username:
        return {"status": "unavailable", "note": "requests no disponible"}
    try:
        r = requests.get(
            f"https://www.reddit.com/user/{username}/about.json",
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
        )
        if r.status_code == 404:
            return {"status": "not_found", "note": "Perfil no encontrado en Reddit."}
        if r.status_code in (403, 429):
            return {"status": "rate_limited", "note": "Reddit limitó la consulta pública."}
        if r.status_code != 200:
            return {"status": "unavailable", "note": f"Reddit devolvió HTTP {r.status_code}."}
        d = (r.json() or {}).get("data") or {}
        sub = d.get("subreddit") or {}
        detalles = {}
        if sub.get("title"):
            detalles["Título público"] = str(sub["title"])[:160]
        if sub.get("public_description"):
            detalles["Descripción"] = str(sub["public_description"])[:220]
        if d.get("total_karma") is not None:
            detalles["Karma total"] = str(d["total_karma"])
        return {
            "status": "public_ok",
            "title": sub.get("title") or d.get("name") or username,
            "description": sub.get("public_description") or "",
            "avatar": (d.get("icon_img") or "").split("?")[0],
            "details": detalles,
            "note": "Metadatos obtenidos desde el JSON público de Reddit.",
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "note": f"Error de red: {exc.__class__.__name__}"}


# Para qué sirve: inspecciona metadatos públicos de una URL social respetando muros de login sin intentar evadirlos.
# Cómo funciona: marca `login_wall` para dominios cerrados, usa APIs públicas en GitHub/Reddit o parsea `<meta og:*>`/`<title>` en otros sitios.
def fetch_public_metadata(url: str, timeout: int = 8) -> dict:
    """
    Obtiene únicamente metadatos públicos abiertos de una URL de red social:
      1. Si el dominio está protegido por muro de login (Facebook, Instagram, LinkedIn, X),
         retorna inmediatamente `login_wall` sin intentar rasparlo.
      2. Si es GitHub o Reddit, usa sus endpoints públicos oficiales.
      3. En otros sitios públicos, lee únicamente <title> y Open Graph (<meta og:*>)
         respetando redirecciones a login.
    """
    host = _clean_host(url)
    if any(host == d or host.endswith("." + d) for d in LOGIN_WALL_DOMAINS):
        return {
            "status": "login_wall",
            "title": "",
            "description": "",
            "avatar": "",
            "details": {},
            "note": "Requiere revisión manual en navegador (plataforma protegida por muro de inicio de sesión).",
        }

    user = _extract_username_from_url(url)
    if host == "github.com" and user:
        return fetch_github_public(user, timeout=timeout)
    if host == "reddit.com" and user:
        return fetch_reddit_public(user, timeout=timeout)

    if not REQUESTS_OK:
        return {"status": "unavailable", "title": "", "description": "", "avatar": "", "details": {},
                "note": "requests no instalado."}

    try:
        r = requests.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept-Language": "es,en;q=0.8"},
            timeout=timeout,
            allow_redirects=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "unavailable", "title": "", "description": "", "avatar": "", "details": {},
                "note": f"No accesible automáticamente ({exc.__class__.__name__})."}

    # Detectamos si la plataforma redirigió a un muro de inicio de sesión
    final_path = urlparse(r.url).path.lower()
    if any(m in final_path for m in _LOGIN_PATH_MARKERS) or r.status_code in (401, 403):
        return {
            "status": "login_wall",
            "title": "",
            "description": "",
            "avatar": "",
            "details": {},
            "note": "Requiere revisión manual (muro de inicio de sesión o acceso restringido).",
        }
    if r.status_code != 200:
        return {
            "status": "unavailable",
            "title": "",
            "description": "",
            "avatar": "",
            "details": {},
            "note": f"Respuesta HTTP {r.status_code}.",
        }

    # Parseamos solo los primeros 120 KB de HTML buscando Open Graph y <title>
    parser = _MetaParser()
    parser.feed(r.text[:120_000])
    title = parser.meta.get("og:title") or parser.meta.get("twitter:title") or parser.title
    desc = parser.meta.get("og:description") or parser.meta.get("twitter:description") or parser.meta.get("description") or ""
    img = parser.meta.get("og:image") or ""

    detalles = {}
    if parser.meta.get("og:site_name"):
        detalles["Plataforma"] = parser.meta["og:site_name"]
    if title:
        detalles["Título público"] = title
    if desc:
        detalles["Descripción pública"] = desc

    return {
        "status": "public_ok" if (title or desc) else "minimal",
        "title": title,
        "description": desc,
        "avatar": img,
        "details": detalles,
        "note": "Metadatos Open Graph públicos visibles sin iniciar sesión." if (title or desc) else "Página accesible sin metadatos Open Graph detallados.",
    }


# Para qué sirve: enriquece perfiles sociales detectados con metadatos abiertos hasta un máximo configurable.
# Cómo funciona: filtra con `is_social_profile`, consulta `fetch_public_metadata` para los primeros `max_checks` y marca el resto `skipped`.
def enrich_social_profiles(profiles: list, max_checks: int = 12, fetch_online: bool = True) -> list:
    """
    Filtra los perfiles correspondientes a redes sociales y enriquece hasta `max_checks`
    con metadatos públicos abiertos (priorizando confianza ALTA y MEDIA).
    """
    sociales = [p for p in (profiles or []) if is_social_profile(p.get("sitio", ""), p.get("url", ""))]
    enriquecidos = []

    for idx, p in enumerate(sociales):
        item = dict(p)
        if fetch_online and idx < max_checks:
            meta = fetch_public_metadata(p["url"])
        else:
            meta = {
                "status": "skipped",
                "title": "",
                "description": "",
                "avatar": "",
                "details": {},
                "note": "Inspección de metadatos omitida (límite alcanzado o desactivada).",
            }
        item["public_meta"] = meta
        enriquecidos.append(item)

    return enriquecidos


# ---------------------------------------------------------------------------
# Evaluación defensiva de exposición y privacidad
# ---------------------------------------------------------------------------
# Para qué sirve: cuantifica la exposición pública del objetivo y sugiere acciones defensivas concretas.
# Cómo funciona: calcula un puntaje `0..100` ponderando perfiles `ALTA`, redes sociales, señales y brechas, y clasifica en `ALTO`/`MODERADO`/`BAJO`.
def build_privacy_assessment(profiles: list = None, emails: dict = None,
                             breaches: dict = None, signals: dict = None) -> dict:
    """
    Calcula un resumen defensivo de exposición pública a partir de los hallazgos
    y sugiere medidas concretas para reducir la huella digital.
    """
    profiles = profiles or []
    emails = emails or {}
    signals = signals or {}

    alta = sum(1 for p in profiles if p.get("confianza") == "ALTA")
    sociales = sum(1 for p in profiles if is_social_profile(p.get("sitio", ""), p.get("url", "")))
    tiene_brechas = bool((breaches or {}).get("breached"))
    pwd_exp = bool((breaches or {}).get("passwords_exposed"))
    tel_exp = bool((breaches or {}).get("phone_exposed"))

    # Puntaje simple y transparente (0..100)
    score = 0
    score += min(35, alta * 7)
    score += min(20, sociales * 4)
    if signals:
        score += 15
    if tiene_brechas:
        score += 20
    if pwd_exp:
        score += 10
    score = min(100, score)

    if score >= 65:
        nivel = "ALTO"
    elif score >= 30:
        nivel = "MODERADO"
    else:
        nivel = "BAJO"

    hallazgos = [
        f"Perfiles públicos con confianza ALTA: {alta} (de {len(profiles)} totales).",
        f"Presencia en redes sociales detectadas: {sociales}.",
    ]
    if signals:
        hallazgos.append(f"Metadatos públicos correlacionables: {', '.join(signals.keys())}.")
    if tiene_brechas:
        hallazgos.append(
            f"Correo expuesto en {breaches.get('breach_count', 0)} brecha(s)"
            + (" (incluye contraseñas/hashes)." if pwd_exp else ".")
        )
    if tel_exp:
        hallazgos.append("Número telefónico indicado entre los datos filtrados de alguna brecha.")

    acciones = []
    if alta >= 3:
        acciones.append("Evitar reutilizar el mismo nombre de usuario en múltiples plataformas para reducir correlación cruzada.")
    if sociales >= 1:
        acciones.append("Revisar la configuración de privacidad de cada red social detectada (visibilidad pública de bio, foto y enlaces).")
    if signals:
        acciones.append("Eliminar datos sensibles (ubicación exacta, empresa o nombre completo) de perfiles públicos donde no sea necesario.")
    if tiene_brechas:
        acciones.append("Rotar contraseñas afectadas y habilitar 2FA con app autenticadora en correo y redes sociales.")
    if not acciones:
        acciones.append("Huella pública acotada en esta búsqueda; mantener contraseñas únicas y revisión periódica.")

    return {
        "level": nivel,
        "score": score,
        "findings": hallazgos,
        "actions": acciones,
    }


# ---------------------------------------------------------------------------
# Grafo de huella digital (nodos y aristas para visualización SVG en la Web UI)
# ---------------------------------------------------------------------------
# Para qué sirve: genera el grafo `{nodes, edges}` de huella digital para visualizar conexiones en la interfaz web.
# Cómo funciona: crea nodo central `root` y enlaza hasta `max_profiles` perfiles, correos, teléfono, brechas y señales.
def build_footprint_graph(target: str, profiles: list = None, emails: dict = None,
                          phone: dict = None, breaches: dict = None, signals: dict = None,
                          max_profiles: int = 24) -> dict:
    """
    Construye un grafo `{nodes, edges}` que conecta el objetivo investigado con sus
    hallazgos públicos (perfiles por confianza, correos, teléfono, brechas y señales)
    para renderizarlo de forma interactiva en la interfaz web.
    """
    nodes = [{
        "id": "root",
        "label": target,
        "type": "target",
        "detail": f"Objetivo analizado: {target}",
        "url": None,
        "confidence": None,
    }]
    edges = []

    # Perfiles públicos (limitamos visualmente a los más relevantes para mantener el grafo legible)
    for i, p in enumerate((profiles or [])[:max_profiles]):
        nid = f"prof_{i}"
        es_social = is_social_profile(p.get("sitio", ""), p.get("url", ""))
        nodes.append({
            "id": nid,
            "label": p.get("sitio", "Sitio"),
            "type": "social" if es_social else "profile",
            "confidence": p.get("confianza", "MEDIA"),
            "url": p.get("url"),
            "detail": (
                f"{p.get('sitio')} [{p.get('confianza')}] · Fuentes: {p.get('fuentes', '—')}"
                + (f" · {p['metadata_resumen']}" if p.get("metadata_resumen") else "")
            ),
        })
        edges.append({"source": "root", "target": nid, "label": p.get("confianza", "")})

    # Correos confirmados
    for i, (mail, svcs) in enumerate(list((emails or {}).items())[:8]):
        nid = f"mail_{i}"
        lista = svcs if isinstance(svcs, list) else []
        nodes.append({
            "id": nid,
            "label": mail,
            "type": "email",
            "confidence": "ALTA" if lista else "MEDIA",
            "url": None,
            "detail": f"Correo: {mail} · Servicios Holehe ({len(lista)}): {', '.join(lista[:8]) or 'ninguno'}",
        })
        edges.append({"source": "root", "target": nid, "label": "correo"})

    # Teléfono
    if phone and isinstance(phone, dict) and phone.get("phone"):
        an = phone.get("analysis") or {}
        nodes.append({
            "id": "phone_0",
            "label": phone["phone"],
            "type": "phone",
            "confidence": "ALTA" if an.get("valid") else "MEDIA",
            "url": None,
            "detail": f"Teléfono {phone['phone']} · {an.get('location') or '?'} · {an.get('carrier') or '?'} ({an.get('line_type') or '?'})",
        })
        edges.append({"source": "root", "target": "phone_0", "label": "teléfono"})

    # Brechas de datos (hasta 8 nodos principales)
    for i, b in enumerate(((breaches or {}).get("breaches") or [])[:8]):
        nid = f"breach_{i}"
        nodes.append({
            "id": nid,
            "label": b.get("name", "Brecha"),
            "type": "breach",
            "confidence": "BAJA",
            "url": None,
            "detail": f"Brecha {b.get('name')} ({b.get('date', '—')}) · Datos: {', '.join(b.get('data_classes') or []) or '—'}",
        })
        edges.append({"source": "root", "target": nid, "label": "brecha"})

    # Señales de correlación de identidad (nombres/ubicaciones)
    for i, (k, vals) in enumerate(list((signals or {}).items())[:4]):
        nid = f"sig_{i}"
        resumen = ", ".join(vals[:3]) if isinstance(vals, list) else str(vals)
        nodes.append({
            "id": nid,
            "label": resumen[:24] + ("…" if len(resumen) > 24 else ""),
            "type": "signal",
            "confidence": "ALTA",
            "url": None,
            "detail": f"{k}: {resumen}",
        })
        edges.append({"source": "root", "target": nid, "label": "metadato"})

    return {"nodes": nodes, "edges": edges}
