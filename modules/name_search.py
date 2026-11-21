# -*- coding: utf-8 -*-
"""
Búsqueda legal por Nombre y Ubicación en redes sociales y fuentes públicas abiertas:
  1. Normaliza el nombre (con y sin tildes) y sugiere usernames derivados para pivotar.
  2. Consulta APIs públicas oficiales SIN API key:
       - GitHub Users Search API (filtra por nombre + ubicación/ciudad y enriquece perfiles).
       - ORCID Public API (perfiles públicos académicos/profesionales e instituciones).
       - OpenAlex Authors API (autores y afiliaciones institucionales públicas por país).
       - Wikipedia API (menciones públicas enciclopédicas).
  3. Consulta APIs oficiales de buscadores web (OPCIONAL si configuras clave en `.env`):
       - Google Custom Search JSON API (`GOOGLE_CSE_KEY` + `GOOGLE_CSE_CX`).
       - Brave Search API (`BRAVE_API_KEY`).
     Trae automáticamente resultados indexados públicamente por red social
     (LinkedIn, Facebook, Instagram, X, TikTok, YouTube, GitHub, Pinterest, Medium).
  4. Genera siempre una matriz interactiva de consultas optimizadas (dorks) por red social
     para Google, DuckDuckGo y Bing (sin evadir muros de login ni CAPTCHAs).
  5. Aplica scoring de coincidencia (ALTA / MEDIA / BAJA) para mitigar homónimos.
"""
import os
import re
import unicodedata
from urllib.parse import quote_plus, urlparse

try:
    import requests
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

_USER_AGENT = "OSINT-Auto/2.0 (public-name-search)"

# Redes sociales objetivo para búsqueda indexada pública (dorks y APIs de buscadores)
SOCIAL_PLATFORMS = [
    {"id": "linkedin",  "name": "LinkedIn",   "site": "linkedin.com/in", "category": "Profesional"},
    {"id": "facebook",  "name": "Facebook",   "site": "facebook.com",    "category": "Red social"},
    {"id": "instagram", "name": "Instagram",  "site": "instagram.com",   "category": "Red social"},
    {"id": "x",         "name": "X / Twitter","site": "x.com",           "category": "Red social"},
    {"id": "tiktok",    "name": "TikTok",     "site": "tiktok.com",      "category": "Red social"},
    {"id": "youtube",   "name": "YouTube",    "site": "youtube.com",     "category": "Video"},
    {"id": "github",    "name": "GitHub",     "site": "github.com",      "category": "Tecnología"},
    {"id": "pinterest", "name": "Pinterest",  "site": "pinterest.com",   "category": "Red social"},
    {"id": "medium",    "name": "Medium",     "site": "medium.com",      "category": "Publicaciones"},
    {"id": "threads",   "name": "Threads",    "site": "threads.net",     "category": "Red social"},
]

# Mapeo rápido de países comunes a código ISO-2 (usado en OpenAlex/ORCID)
_COUNTRY_ISO = {
    "colombia": "CO", "co": "CO",
    "mexico": "MX", "méxico": "MX", "mx": "MX",
    "espana": "ES", "españa": "ES", "es": "ES",
    "estados unidos": "US", "usa": "US", "us": "US",
    "argentina": "AR", "ar": "AR",
    "chile": "CL", "cl": "CL",
    "peru": "PE", "perú": "PE", "pe": "PE",
    "venezuela": "VE", "ve": "VE",
    "ecuador": "EC", "ec": "EC",
    "uruguay": "UY", "uy": "UY",
}


# Para qué sirve: informa qué APIs públicas y de buscadores están disponibles para búsqueda por nombre.
# Cómo funciona: revisa `REQUESTS_OK` y la presencia de variables `GOOGLE_CSE_*` y `BRAVE_API_KEY` en el entorno.
def name_search_capabilities() -> dict:
    """Informa qué APIs de búsqueda por nombre están disponibles/configuradas."""
    return {
        "github_public": REQUESTS_OK,
        "orcid_public": REQUESTS_OK,
        "openalex_public": REQUESTS_OK,
        "wikipedia_public": REQUESTS_OK,
        "google_cse": bool(os.environ.get("GOOGLE_CSE_KEY", "").strip() and os.environ.get("GOOGLE_CSE_CX", "").strip()),
        "brave_search": bool(os.environ.get("BRAVE_API_KEY", "").strip()),
    }


# Para qué sirve: elimina tildes y diacríticos para comparar nombres propios sin sensibilidad a acentos.
# Cómo funciona: normaliza en NFKD con `unicodedata` y descarta caracteres combinantes.
def strip_accents(text: str) -> str:
    """Elimina tildes/diacríticos para comparar nombres de forma insensible a acentos."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# Para qué sirve: extrae variantes limpias del nombre y deriva posibles usernames para pivotar búsquedas.
# Cómo funciona: tokeniza el nombre ASCII y combina primer nombre + último/primer apellido en formatos comunes (`3..30` chars).
def build_name_variants(full_name: str) -> dict:
    """
    Genera variantes limpias del nombre y sugiere posibles usernames derivados
    para poder pivotar luego a la búsqueda por Username / Redes Sociales.
    """
    limpio = " ".join((full_name or "").strip().split())
    sin_tildes = strip_accents(limpio)
    tokens = [re.sub(r"[^a-z0-9]", "", t.lower()) for t in sin_tildes.split()]
    tokens = [t for t in tokens if t]

    usernames = []
    if len(tokens) >= 2:
        first, last = tokens[0], tokens[-1]
        usernames = [
            f"{first}{last}",
            f"{first}.{last}",
            f"{first}_{last}",
            f"{first[0]}{last}",
            f"{first}{last[0]}",
        ]
        if len(tokens) >= 3:
            # Ej: nombre + primer apellido (muy frecuente en países hispanohablantes)
            ap1 = tokens[1]
            usernames.extend([f"{first}{ap1}", f"{first}.{ap1}", f"{first}_{ap1}"])
    elif len(tokens) == 1:
        usernames = [tokens[0]]

    return {
        "original": limpio,
        "ascii": sin_tildes,
        "tokens": tokens,
        "suggested_usernames": list(dict.fromkeys(u for u in usernames if 3 <= len(u) <= 30)),
    }


# Para qué sirve: puntúa coincidencias (`ALTA`/`MEDIA`/`BAJA`) para mitigar falsos positivos por homónimos.
# Cómo funciona: comprueba si todo el nombre o todos sus tokens aparecen en `text_blob` y si coincide ubicación o keyword.
def _score_match(text_blob: str, full_name: str, city: str = "", country: str = "", keyword: str = "") -> str:
    """
    Evalúa el nivel de coincidencia para reducir falsos positivos por homónimos:
      - ALTA:  coincide el nombre completo (o todos sus tokens) Y coincide la ciudad/zona/país o palabra clave.
      - MEDIA: coincide el nombre completo (o todos los tokens), pero la ubicación no figura explícitamente.
      - BAJA:  coincidencia parcial (posible homónimo; verificar manualmente).
    """
    blob = strip_accents(text_blob).lower()
    name_norm = strip_accents(full_name).lower().strip()
    tokens = [t for t in re.split(r"\s+", name_norm) if len(t) >= 2]

    exact_name = bool(name_norm and name_norm in blob)
    all_tokens = bool(tokens and all(t in blob for t in tokens))

    loc_terms = [strip_accents(x).lower().strip() for x in (city, country, keyword) if (x or "").strip()]
    loc_hit = any(term in blob for term in loc_terms) if loc_terms else False

    if (exact_name or all_tokens) and (loc_hit or not loc_terms):
        return "ALTA"
    if exact_name or all_tokens:
        return "MEDIA"
    return "BAJA"


# Para qué sirve: identifica el nombre legible de la red social o plataforma académica a partir de una URL.
# Cómo funciona: compara el dominio contra `SOCIAL_PLATFORMS` y sufijos conocidos (`orcid.org`, `openalex.org`, `wikipedia.org`).
def _detect_network_from_url(url: str) -> str:
    """Identifica el nombre legible de la red social a partir del dominio de la URL."""
    host = urlparse(url or "").netloc.lower().replace("www.", "")
    for p in SOCIAL_PLATFORMS:
        dom = p["site"].split("/")[0]
        if host == dom or host.endswith("." + dom):
            return p["name"]
    if "orcid.org" in host:
        return "ORCID (Académico)"
    if "openalex.org" in host:
        return "OpenAlex (Publicaciones)"
    if "wikipedia.org" in host:
        return "Wikipedia"
    return host or "Web pública"


# ---------------------------------------------------------------------------
# 1) APIs públicas oficiales SIN API key
# ---------------------------------------------------------------------------
# Para qué sirve: busca usuarios públicos en GitHub filtrando por nombre completo y ciudad/país.
# Cómo funciona: consulta `/search/users`, enriquece los primeros 5 perfiles en `/users/{login}` y puntúa con `_score_match`.
def search_github_by_name(full_name: str, city: str = "", country: str = "",
                          max_results: int = 8, timeout: int = 8) -> list:
    """
    Busca perfiles públicos en GitHub por nombre real y ubicación (`location:...`)
    usando la API pública oficial de GitHub.
    """
    if not REQUESTS_OK or not full_name:
        return []

    loc = (city or country or "").strip()
    q_parts = [f'"{full_name}" in:name']
    if loc:
        q_parts.append(f'location:"{loc}"')
    query = " ".join(q_parts)

    try:
        r = requests.get(
            "https://api.github.com/search/users",
            params={"q": query, "per_page": min(max_results, 10)},
            headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
            timeout=timeout,
        )
        if r.status_code != 200:
            return []
        items = (r.json() or {}).get("items") or []
    except Exception:  # noqa: BLE001
        return []

    resultados = []
    # Enriquecemos los primeros perfiles (hasta 5 para respetar rate-limit anónimo de GitHub)
    for idx, it in enumerate(items[:max_results]):
        login = it.get("login") or ""
        html_url = it.get("html_url") or ""
        nombre = login
        ubicacion = loc or "—"
        snippet_parts = []
        avatar = it.get("avatar_url") or ""

        if idx < 5 and login:
            try:
                pr = requests.get(
                    f"https://api.github.com/users/{login}",
                    headers={"User-Agent": _USER_AGENT, "Accept": "application/vnd.github+json"},
                    timeout=6,
                )
                if pr.status_code == 200:
                    d = pr.json()
                    nombre = d.get("name") or login
                    ubicacion = d.get("location") or ubicacion
                    if d.get("company"):
                        snippet_parts.append(f"Org: {d['company']}")
                    if d.get("bio"):
                        snippet_parts.append(str(d["bio"])[:140])
            except Exception:  # noqa: BLE001
                pass

        snippet = " · ".join(snippet_parts) or f"Usuario GitHub: @{login}"
        conf = _score_match(f"{nombre} {login} {ubicacion} {snippet}", full_name, city, country)
        resultados.append({
            "person_name": nombre,
            "username": login,
            "network": "GitHub",
            "location": ubicacion,
            "url": html_url,
            "snippet": snippet,
            "avatar": avatar,
            "match": conf,
            "source": "GitHub API pública",
        })
    return resultados


# Para qué sirve: localiza perfiles académicos públicos e instituciones asociadas en ORCID sin API key.
# Cómo funciona: consulta `/v3.0/expanded-search/` por `given-names`/`family-name` y puntúa coincidencia con `_score_match`.
def search_orcid_by_name(full_name: str, city: str = "", country: str = "",
                         max_results: int = 6, timeout: int = 8) -> list:
    """
    Busca perfiles públicos académicos/profesionales en ORCID (API pública oficial, sin clave).
    Útil para encontrar investigadores, docentes, estudiantes y autores con afiliación institucional.
    """
    if not REQUESTS_OK or not full_name:
        return []
    partes = full_name.strip().split()
    if len(partes) >= 2:
        q = f'given-names:"{partes[0]}" AND family-name:"{" ".join(partes[1:])}"'
    else:
        q = f'"{full_name}"'

    try:
        r = requests.get(
            "https://pub.orcid.org/v3.0/expanded-search/",
            params={"q": q, "rows": max_results},
            headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
            timeout=timeout,
        )
        if r.status_code != 200:
            return []
        items = (r.json() or {}).get("expanded-result") or []
    except Exception:  # noqa: BLE001
        return []

    resultados = []
    for it in items:
        oid = it.get("orcid-id")
        if not oid:
            continue
        nombre = " ".join(x for x in (it.get("given-names"), it.get("family-names")) if x) or full_name
        insts = [str(x) for x in (it.get("institution-name") or []) if x]
        snippet = f"Instituciones públicas: {', '.join(insts[:3])}" if insts else "Perfil académico público en ORCID"
        conf = _score_match(f"{nombre} {snippet}", full_name, city, country)
        resultados.append({
            "person_name": nombre,
            "username": oid,
            "network": "ORCID (Académico)",
            "location": ", ".join(insts[:2]) or "—",
            "url": f"https://orcid.org/{oid}",
            "snippet": snippet,
            "avatar": "",
            "match": conf,
            "source": "ORCID API pública",
        })
    return resultados


# Para qué sirve: consulta autores y afiliaciones académicas públicas en el índice abierto OpenAlex.
# Cómo funciona: busca por nombre en `api.openalex.org/authors` y filtra por código ISO-2 del país si se conoce en `_COUNTRY_ISO`.
def search_openalex_by_name(full_name: str, city: str = "", country: str = "",
                            max_results: int = 6, timeout: int = 8) -> list:
    """
    Busca autores públicos en OpenAlex (índice abierto de publicaciones y afiliaciones).
    Si el usuario indicó país, filtra por código ISO-2 de la última institución conocida.
    """
    if not REQUESTS_OK or not full_name:
        return []
    params = {"search": full_name, "per-page": max_results}
    iso = _COUNTRY_ISO.get(strip_accents(country or "").lower().strip())
    if iso:
        params["filter"] = f"last_known_institutions.country_code:{iso}"

    try:
        r = requests.get(
            "https://api.openalex.org/authors",
            params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
        )
        if r.status_code != 200:
            return []
        items = (r.json() or {}).get("results") or []
    except Exception:  # noqa: BLE001
        return []

    resultados = []
    for it in items:
        nombre = it.get("display_name") or full_name
        insts = it.get("last_known_institutions") or []
        inst_txt = ", ".join(
            f"{i.get('display_name', '')} ({i.get('country_code', '')})".strip()
            for i in insts[:2] if i.get("display_name")
        )
        url = it.get("orcid") or it.get("id") or ""
        obras = it.get("works_count", 0)
        snippet = f"Publicaciones: {obras}" + (f" · Afiliación: {inst_txt}" if inst_txt else "")
        conf = _score_match(f"{nombre} {inst_txt}", full_name, city, country)
        resultados.append({
            "person_name": nombre,
            "username": "",
            "network": "OpenAlex (Publicaciones)",
            "location": inst_txt or (iso or "—"),
            "url": url,
            "snippet": snippet,
            "avatar": "",
            "match": conf,
            "source": "OpenAlex API pública",
        })
    return resultados


# Para qué sirve: detecta menciones enciclopédicas públicas en Wikipedia en español por nombre y zona.
# Cómo funciona: consulta `es.wikipedia.org/w/api.php?action=query&list=search`, limpia HTML del snippet y evalúa coincidencia.
def search_wikipedia_by_name(full_name: str, city: str = "", country: str = "",
                             max_results: int = 4, timeout: int = 8) -> list:
    """Consulta menciones públicas en Wikipedia en español para figuras públicas o autores."""
    if not REQUESTS_OK or not full_name:
        return []
    extra = " ".join(x for x in (city, country) if x).strip()
    q = f'"{full_name}" {extra}'.strip()
    try:
        r = requests.get(
            "https://es.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": q, "srlimit": max_results, "format": "json"},
            headers={"User-Agent": _USER_AGENT},
            timeout=timeout,
        )
        if r.status_code != 200:
            return []
        items = ((r.json() or {}).get("query") or {}).get("search") or []
    except Exception:  # noqa: BLE001
        return []

    resultados = []
    for it in items:
        titulo = it.get("title") or ""
        snippet = re.sub(r"<[^>]+>", "", it.get("snippet") or "")
        url = f"https://es.wikipedia.org/wiki/{quote_plus(titulo.replace(' ', '_'))}"
        conf = _score_match(f"{titulo} {snippet}", full_name, city, country)
        resultados.append({
            "person_name": titulo,
            "username": "",
            "network": "Wikipedia",
            "location": extra or "—",
            "url": url,
            "snippet": snippet[:200],
            "avatar": "",
            "match": conf,
            "source": "Wikipedia API pública",
        })
    return resultados


# ---------------------------------------------------------------------------
# 2) APIs oficiales de buscadores web (opcionales vía `.env`)
# ---------------------------------------------------------------------------
# Para qué sirve: busca perfiles indexados en redes sociales mediante Google Custom Search API si hay clave configurada.
# Cómo funciona: arma una consulta `(site:...) "Nombre" "ciudad"` con las plataformas seleccionadas y califica cada enlace.
def search_google_cse(full_name: str, city: str = "", country: str = "",
                      keyword: str = "", platforms: list = None, timeout: int = 10) -> list:
    """
    Usa Google Custom Search JSON API (si `GOOGLE_CSE_KEY` y `GOOGLE_CSE_CX` están en `.env`)
    para traer automáticamente perfiles públicamente indexados en redes sociales.
    """
    key = os.environ.get("GOOGLE_CSE_KEY", "").strip()
    cx = os.environ.get("GOOGLE_CSE_CX", "").strip()
    if not (REQUESTS_OK and key and cx):
        return []

    elegidas = platforms or [p["site"] for p in SOCIAL_PLATFORMS[:6]]
    site_expr = " OR ".join(f"site:{s}" for s in elegidas[:6])
    partes = [f'({site_expr})', f'"{full_name}"']
    for extra in (city, country, keyword):
        if (extra or "").strip():
            partes.append(f'"{extra.strip()}"')
    q = " ".join(partes)

    try:
        r = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": key, "cx": cx, "q": q, "num": 10},
            timeout=timeout,
        )
        if r.status_code != 200:
            return []
        items = (r.json() or {}).get("items") or []
    except Exception:  # noqa: BLE001
        return []

    resultados = []
    for it in items:
        link = it.get("link") or ""
        title = it.get("title") or full_name
        snippet = (it.get("snippet") or "").replace("\n", " ").strip()
        red = _detect_network_from_url(link)
        conf = _score_match(f"{title} {snippet} {link}", full_name, city, country, keyword)
        resultados.append({
            "person_name": title.split(" - ")[0].split(" | ")[0][:90],
            "username": "",
            "network": red,
            "location": ", ".join(x for x in (city, country) if x) or "—",
            "url": link,
            "snippet": snippet[:220],
            "avatar": "",
            "match": conf,
            "source": "Google Custom Search API",
        })
    return resultados


# Para qué sirve: busca perfiles públicos en redes sociales usando Brave Search API si hay clave en `.env`.
# Cómo funciona: construye consulta `site:` + nombre + zona, solicita `web/search` y evalúa cada resultado con `_score_match`.
def search_brave_api(full_name: str, city: str = "", country: str = "",
                     keyword: str = "", platforms: list = None, timeout: int = 10) -> list:
    """
    Usa Brave Search API oficial (si `BRAVE_API_KEY` está en `.env`)
    para encontrar perfiles públicos indexados en redes sociales sin CAPTCHAs.
    """
    key = os.environ.get("BRAVE_API_KEY", "").strip()
    if not (REQUESTS_OK and key):
        return []

    elegidas = platforms or [p["site"] for p in SOCIAL_PLATFORMS[:6]]
    site_expr = " OR ".join(f"site:{s}" for s in elegidas[:6])
    partes = [f'({site_expr})', f'"{full_name}"']
    for extra in (city, country, keyword):
        if (extra or "").strip():
            partes.append(f'"{extra.strip()}"')
    q = " ".join(partes)

    try:
        r = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": q, "count": 10},
            headers={"Accept": "application/json", "X-Subscription-Token": key, "User-Agent": _USER_AGENT},
            timeout=timeout,
        )
        if r.status_code != 200:
            return []
        items = ((r.json() or {}).get("web") or {}).get("results") or []
    except Exception:  # noqa: BLE001
        return []

    resultados = []
    for it in items:
        link = it.get("url") or ""
        title = it.get("title") or full_name
        snippet = (it.get("description") or "").replace("\n", " ").strip()
        red = _detect_network_from_url(link)
        conf = _score_match(f"{title} {snippet} {link}", full_name, city, country, keyword)
        resultados.append({
            "person_name": title.split(" - ")[0].split(" | ")[0][:90],
            "username": "",
            "network": red,
            "location": ", ".join(x for x in (city, country) if x) or "—",
            "url": link,
            "snippet": snippet[:220],
            "avatar": "",
            "match": conf,
            "source": "Brave Search API",
        })
    return resultados


# ---------------------------------------------------------------------------
# 3) Matriz interactiva de consultas optimizadas (Dorks legales por red social)
# ---------------------------------------------------------------------------
# Para qué sirve: genera dorks legales por red social, PDFs y prensa con enlaces directos a buscadores.
# Cómo funciona: arma `site:<red> "<nombre>"` + filtros entrecomillados y codifica URLs para Google, DuckDuckGo y Bing.
def build_social_dorks(full_name: str, city: str = "", country: str = "",
                       keyword: str = "", selected_ids: list = None) -> list:
    """
    Construye consultas de búsqueda estructuradas por red social y fuentes públicas
    con enlaces directos a Google, DuckDuckGo y Bing (sin automatización invasiva ni CAPTCHAs).
    """
    filtros = [f'"{x.strip()}"' for x in (city, country, keyword) if (x or "").strip()]
    sufijo = (" " + " ".join(filtros)) if filtros else ""

    elegidas = SOCIAL_PLATFORMS
    if selected_ids:
        sel = set(selected_ids)
        elegidas = [p for p in SOCIAL_PLATFORMS if p["id"] in sel] or SOCIAL_PLATFORMS

    dorks = []
    for p in elegidas:
        q = f'site:{p["site"]} "{full_name}"{sufijo}'
        dorks.append({
            "id": p["id"],
            "network": p["name"],
            "category": p["category"],
            "query": q,
            "google_url": f"https://www.google.com/search?q={quote_plus(q)}",
            "duckduckgo_url": f"https://duckduckgo.com/?q={quote_plus(q)}",
            "bing_url": f"https://www.bing.com/search?q={quote_plus(q)}",
        })

    # Añadimos dos consultas complementarias de fuentes públicas abiertas (PDFs y prensa)
    for extra_id, label, cat, q in (
        ("docs_pdf", "Documentos públicos (PDF)", "Documentos", f'"{full_name}"{sufijo} filetype:pdf'),
        ("news_web", "Menciones y prensa pública", "Noticias", f'"{full_name}"{sufijo}'),
    ):
        dorks.append({
            "id": extra_id,
            "network": label,
            "category": cat,
            "query": q,
            "google_url": f"https://www.google.com/search?q={quote_plus(q)}",
            "duckduckgo_url": f"https://duckduckgo.com/?q={quote_plus(q)}",
            "bing_url": f"https://www.bing.com/search?q={quote_plus(q)}",
        })
    return dorks


# ---------------------------------------------------------------------------
# 4) Orquestador principal + formatos de reporte
# ---------------------------------------------------------------------------
# Para qué sirve: orquesta la búsqueda completa por nombre real y ubicación combinando APIs abiertas, buscadores y dorks.
# Cómo funciona: consulta GitHub/ORCID/OpenAlex/Wikipedia + Google/Brave si existen, deduplica por URL y prioriza ALTA -> MEDIA -> BAJA.
def run_name_location_search(full_name: str, city: str = "", country: str = "",
                             keyword: str = "", selected_ids: list = None) -> dict:
    """
    Ejecuta búsqueda consolidada por Nombre + Ubicación/Zona:
      - Consulta APIs públicas abiertas (GitHub, ORCID, OpenAlex, Wikipedia).
      - Si hay API key en `.env`, consulta además Google CSE y/o Brave Search por red social.
      - Deduplica por URL, ordena por nivel de coincidencia (ALTA -> MEDIA -> BAJA)
        y construye la matriz de consultas directas por red social.
    """
    variants = build_name_variants(full_name)
    nombre_limpio = variants["original"]
    selected_sites = [
        p["site"] for p in SOCIAL_PLATFORMS
        if not selected_ids or p["id"] in set(selected_ids)
    ]

    brutos = []
    sources_used = []

    # APIs públicas gratuitas
    gh = search_github_by_name(nombre_limpio, city, country)
    sources_used.append("GitHub API pública")
    brutos.extend(gh)

    orcid = search_orcid_by_name(nombre_limpio, city, country)
    sources_used.append("ORCID API pública")
    brutos.extend(orcid)

    oalex = search_openalex_by_name(nombre_limpio, city, country)
    sources_used.append("OpenAlex API pública")
    brutos.extend(oalex)

    wiki = search_wikipedia_by_name(nombre_limpio, city, country)
    sources_used.append("Wikipedia API pública")
    brutos.extend(wiki)

    # APIs opcionales de buscadores web (si el usuario agregó claves en `.env`)
    caps = name_search_capabilities()
    if caps["google_cse"]:
        sources_used.append("Google Custom Search API")
        brutos.extend(search_google_cse(nombre_limpio, city, country, keyword, selected_sites))
    if caps["brave_search"]:
        sources_used.append("Brave Search API")
        brutos.extend(search_brave_api(nombre_limpio, city, country, keyword, selected_sites))

    # Deduplicación por URL normalizada
    orden_match = {"ALTA": 0, "MEDIA": 1, "BAJA": 2}
    vistas, candidatos = set(), []
    for item in brutos:
        u = (item.get("url") or "").strip().rstrip("/")
        clave = u.lower() if u else f"{item.get('network')}:{item.get('person_name')}".lower()
        if clave in vistas:
            continue
        vistas.add(clave)
        candidatos.append(item)

    candidatos.sort(key=lambda x: (orden_match.get(x.get("match", "BAJA"), 9), x.get("network", "")))

    # Añadimos usernames reales detectados en GitHub a las sugerencias para pivotar
    discovered_usernames = [c["username"] for c in candidatos if c.get("network") == "GitHub" and c.get("username")]
    all_usernames = list(dict.fromkeys(discovered_usernames + variants["suggested_usernames"]))

    dorks = build_social_dorks(nombre_limpio, city, country, keyword, selected_ids)

    summary = {
        "total": len(candidatos),
        "alta": sum(1 for c in candidatos if c["match"] == "ALTA"),
        "media": sum(1 for c in candidatos if c["match"] == "MEDIA"),
        "baja": sum(1 for c in candidatos if c["match"] == "BAJA"),
        "dorks_count": len(dorks),
    }

    return {
        "full_name": nombre_limpio,
        "city": (city or "").strip(),
        "country": (country or "").strip(),
        "keyword": (keyword or "").strip(),
        "variants": variants,
        "suggested_usernames": all_usernames[:10],
        "sources_used": sources_used,
        "search_apis_configured": caps["google_cse"] or caps["brave_search"],
        "summary": summary,
        "candidates": candidatos,
        "dorks": dorks,
        "homonym_notice": (
            "Los resultados por nombre y ubicación pueden incluir homónimos (personas distintas con el mismo nombre). "
            "Verifica siempre foto, ciudad y contexto antes de asumir identidad."
        ),
    }


# Para qué sirve: estructura los hallazgos de búsqueda por nombre en secciones legibles para HTML y PDF.
# Cómo funciona: arma resumen de conteos/usernames, listado `[nivel]` de candidatos y URLs Google de los dorks.
def format_name_report(res: dict) -> dict:
    """Formatea resultados de `run_name_location_search` para HTML y PDF."""
    s = res.get("summary") or {}
    loc = ", ".join(x for x in (res.get("city"), res.get("country")) if x) or "Global"
    resumen = [
        f"Nombre buscado: {res.get('full_name')}",
        f"Ubicación / zona: {loc}",
        f"Palabra clave extra: {res.get('keyword') or '—'}",
        f"Fuentes consultadas automáticamente: {', '.join(res.get('sources_used') or [])}",
        f"Candidatos hallados: {s.get('total', 0)} (ALTA: {s.get('alta', 0)}, MEDIA: {s.get('media', 0)}, BAJA: {s.get('baja', 0)})",
        f"Usernames sugeridos para pivotar: {', '.join(res.get('suggested_usernames') or []) or '—'}",
        f"Aviso de precisión: {res.get('homonym_notice')}",
    ]

    candidatos_lines = [
        f"[{c['match']}] {c['person_name']} ({c['network']} · {c.get('location') or '—'}): {c['url']} — {c.get('snippet', '')}"
        for c in res.get("candidates") or []
    ]
    dorks_lines = [
        f"{d['network']} (Google): {d['google_url']}"
        for d in res.get("dorks") or []
    ]

    return {
        "Resumen de búsqueda por nombre": resumen,
        "Candidatos detectados en APIs públicas": candidatos_lines or ["(sin coincidencias automáticas en APIs públicas; usa la matriz de consultas por red social)"],
        "Consultas optimizadas por red social": dorks_lines,
    }


# Para qué sirve: convierte los candidatos detectados en filas planas para exportar `busqueda_nombre.csv`.
# Cómo funciona: extrae coincidencia, nombre, red, ubicación, URL, fuente y detalle en una lista de dicts.
def name_csv_rows(res: dict) -> list:
    """Genera filas planas para exportar `busqueda_nombre.csv`."""
    return [
        {
            "coincidencia": c.get("match", ""),
            "nombre": c.get("person_name", ""),
            "red_social": c.get("network", ""),
            "ubicacion": c.get("location", ""),
            "url": c.get("url", ""),
            "fuente": c.get("source", ""),
            "detalle": c.get("snippet", ""),
        }
        for c in res.get("candidates") or []
    ]
