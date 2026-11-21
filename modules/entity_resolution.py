# -*- coding: utf-8 -*-
"""
Motor de Resolución de Entidades (Entity Resolution) para OSINT-Auto (Fase 1).

Calcula un puntaje explicable (0–100) para cada perfil vinculado a un caso,
combinando señales verificables con el fin de confirmar coincidencias reales
y alertar sobre posibles homónimos / falsos positivos.

Señales evaluadas:
  1. Confianza OSINT base y coincidencia multi-herramienta (Maigret, Sherlock, Blackbird, etc.).
  2. Singularidad del username (usernames largos/específicos vs. cortos/genéricos).
  3. Enlaces cruzados (un perfil enlaza a otro perfil del mismo caso en su bio o website).
  4. Avatar compartido entre dos o más plataformas del caso.
  5. Coincidencia de nombre visible / nombre real contra las semillas del caso u otros perfiles.
  6. Coincidencia o contradicción de ubicación declarada.
  7. Similitud léxica de biografías entre plataformas del caso.
  8. Referencia a correos o dominios presentes en los identificadores del caso.
"""
import re
import unicodedata
from urllib.parse import urlparse


# Stopwords comunes (ES/EN) para evitar falsas coincidencias al comparar biografías
_STOPWORDS = {
    "para", "como", "pero", "sobre", "entre", "desde", "hasta", "todo", "toda",
    "todos", "todas", "esta", "este", "estos", "estas", "aqui", "donde", "cuando",
    "quien", "cual", "unos", "unas", "unos", "tambien", "solo", "mas", "muy",
    "with", "from", "that", "this", "have", "your", "about", "into", "more",
    "they", "their", "them", "what", "when", "where", "which", "will", "just",
    "official", "account", "profile", "perfil", "cuenta", "oficial", "page",
    "http", "https", "www", "com", "net", "org",
}

_COMMON_SHORT_WORDS = {
    "alex", "juan", "luis", "jose", "maria", "ana", "laura", "david", "carlos",
    "diego", "sofia", "andres", "pablo", "diana", "sara", "paula", "mateo",
    "admin", "user", "test", "info", "dev", "code", "music", "photo", "game",
    "john", "mike", "mark", "paul", "anna", "lisa", "chris", "kevin", "daniel",
}

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)


# Para qué sirve: estandariza cualquier texto eliminando tildes, mayúsculas y espacios sobrantes para comparaciones.
# Cómo funciona: descompone en NFD con `unicodedata`, filtra marcas diacríticas (`Mn`) y colapsa espacios.
def normalize_text(text: str) -> str:
    """Minúsculas sin tildes ni espacios repetidos."""
    if not text:
        return ""
    nfd = unicodedata.normalize("NFD", str(text))
    sin_tildes = "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
    return " ".join(sin_tildes.lower().split())


# Para qué sirve: unifica URLs de perfiles para deduplicarlas y detectar enlaces cruzados entre plataformas.
# Cómo funciona: antepone esquema si falta, quita `www.` del host y elimina `/` finales del path en minúsculas.
def normalize_url(url: str) -> str:
    """Normaliza una URL para deduplicación y detección de enlaces cruzados."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", raw):
        raw = "https://" + raw
    parsed = urlparse(raw)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (parsed.path or "").rstrip("/").lower()
    return f"{host}{path}"


# Para qué sirve: extrae palabras informativas de biografías o ubicaciones descartando stopwords.
# Cómo funciona: normaliza el texto, extrae tokens alfanuméricos de longitud >= `min_len` y filtra `_STOPWORDS`.
def _tokenize(text: str, min_len: int = 3) -> set:
    norm = normalize_text(text)
    tokens = re.findall(r"[a-z0-9]+", norm)
    return {t for t in tokens if len(t) >= min_len and t not in _STOPWORDS}


# Para qué sirve: obtiene tokens alfabéticos individuales de un nombre propio o `display_name`.
# Cómo funciona: normaliza tildes/minúsculas y extrae palabras `[a-z]+` de al menos 2 caracteres.
def _name_tokens(text: str) -> set:
    norm = normalize_text(text)
    tokens = re.findall(r"[a-z]+", norm)
    return {t for t in tokens if len(t) >= 2}


# Para qué sirve: recolecta las URLs salientes declaradas en un perfil para buscar enlaces cruzados.
# Cómo funciona: busca URLs HTTP(S) dentro de `website` y `bio` y las normaliza con `normalize_url`.
def _extract_urls_from_profile(profile: dict) -> set:
    urls = set()
    for field in ("website", "bio"):
        val = profile.get(field) or ""
        for m in _URL_RE.findall(val):
            nu = normalize_url(m)
            if nu:
                urls.add(nu)
        if field == "website" and val.strip():
            nu = normalize_url(val)
            if nu and "." in nu:
                urls.add(nu)
    return urls


# Para qué sirve: pondera la singularidad de un username para penalizar coincidencias débiles por homonimia.
# Cómo funciona: asigna de 4 a 18 puntos según longitud, separadores/dígitos y presencia en `_COMMON_SHORT_WORDS`.
def _username_specificity(username: str) -> tuple:
    """
    Evalúa qué tan distintivo es un username para reducir falsos positivos por homonimia.
    Devuelve (puntos, etiqueta_explicativa, es_generico).
    """
    u = (username or "").strip().lower()
    if not u:
        return 0, None, False
    clean = re.sub(r"[^a-z0-9._-]", "", u)
    has_digit = any(c.isdigit() for c in clean)
    has_sep = any(c in "._-" for c in clean)

    if clean in _COMMON_SHORT_WORDS or (len(clean) <= 5 and not has_digit and not has_sep):
        return 4, f"Username corto o común ('{u}'): riesgo de homónimo", True
    if len(clean) >= 10 and (has_digit or has_sep):
        return 18, f"Username altamente distintivo ('{u}')", False
    if len(clean) >= 8 or (len(clean) >= 6 and (has_digit or has_sep)):
        return 14, f"Username con buena especificidad ('{u}')", False
    return 9, f"Username de especificidad moderada ('{u}')", False


# Para qué sirve: mide la similitud léxica entre dos conjuntos de tokens (p. ej. biografías).
# Cómo funciona: calcula el índice de Jaccard `|A ∩ B| / |A ∪ B|`.
def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


# Para qué sirve: calcula el puntaje explicable (0-100) y nivel de resolución de cada perfil frente a las semillas del caso.
# Cómo funciona: compara pares de perfiles (enlaces cruzados, avatar, bio, nombre), suma evidencias contra semillas y respeta `manual_status`.
def evaluate_case_resolution(
    profiles: list,
    identifiers: list,
    subject_name: str = "",
) -> dict:
    """
    Evalúa todos los perfiles de un caso y devuelve:
      - `profiles`: lista de dicts `{profile_id, score, level, reasons, signals}`
      - `cross_links`: relaciones verificadas entre pares de perfiles
      - `warnings`: advertencias de posibles homónimos o inconsistencias
      - `stats`: métricas agregadas del caso
    """
    # Preparar conjuntos de referencia desde identificadores del caso
    seed_usernames = set()
    seed_emails = set()
    seed_domains = set()
    seed_names = []
    seed_locations = set()

    if subject_name and subject_name.strip():
        seed_names.append(_name_tokens(subject_name))

    for ident in identifiers or []:
        kind = (ident.get("kind") or "").lower()
        val = (ident.get("value") or "").strip()
        if not val:
            continue
        if kind == "username":
            seed_usernames.add(val.lower().lstrip("@"))
        elif kind == "email":
            seed_emails.add(val.lower())
            if "@" in val:
                dom = val.split("@", 1)[1].lower()
                if dom not in {"gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "icloud.com", "proton.me", "protonmail.com"}:
                    seed_domains.add(dom)
        elif kind == "domain":
            seed_domains.add(val.lower().removeprefix("www."))
        elif kind in ("full_name", "name"):
            toks = _name_tokens(val)
            if toks:
                seed_names.append(toks)
        elif kind == "location":
            toks = _tokenize(val, min_len=3)
            seed_locations.update(toks)

    # Pre-cálculo por perfil
    prep = []
    url_to_pids = {}
    avatar_to_pids = {}
    location_counts = {}

    for p in profiles or []:
        pid = p.get("id")
        norm_u = normalize_url(p.get("url") or "")
        ext_urls = _extract_urls_from_profile(p)
        avatar = (p.get("avatar_url") or "").strip().lower()
        bio_toks = _tokenize(p.get("bio") or "", min_len=4)
        name_toks = _name_tokens(p.get("display_name") or "")
        loc_toks = _tokenize(p.get("location") or "", min_len=3)

        if norm_u:
            url_to_pids.setdefault(norm_u, []).append(pid)
        if avatar and avatar.startswith("http"):
            avatar_to_pids.setdefault(avatar, []).append(pid)
        for lt in loc_toks:
            location_counts[lt] = location_counts.get(lt, 0) + 1

        prep.append({
            "raw": p,
            "id": pid,
            "site": p.get("site") or "Sitio",
            "norm_url": norm_u,
            "ext_urls": ext_urls,
            "avatar": avatar,
            "bio_toks": bio_toks,
            "name_toks": name_toks,
            "loc_toks": loc_toks,
            "username": (p.get("username") or "").strip().lower().lstrip("@"),
        })

    # Detectar relaciones cruzadas entre pares de perfiles
    cross_links = []
    pair_seen = set()
    profile_links_map = {item["id"]: [] for item in prep}

    for i, a in enumerate(prep):
        for j in range(i + 1, len(prep)):
            b = prep[j]
            pair_key = (min(a["id"], b["id"]), max(a["id"], b["id"]))

            # 1) Enlace cruzado directo (bio/website de A apunta a B o viceversa)
            a_links_b = bool(b["norm_url"] and any(b["norm_url"] in u or u in b["norm_url"] for u in a["ext_urls"]))
            b_links_a = bool(a["norm_url"] and any(a["norm_url"] in u or u in a["norm_url"] for u in b["ext_urls"]))
            if a_links_b or b_links_a:
                reason = f"Enlace cruzado directo entre {a['site']} y {b['site']}"
                profile_links_map[a["id"]].append(("cross_link", b["site"], reason))
                profile_links_map[b["id"]].append(("cross_link", a["site"], reason))
                if (*pair_key, "cross_link") not in pair_seen:
                    pair_seen.add((*pair_key, "cross_link"))
                    cross_links.append({
                        "type": "cross_link",
                        "from_id": a["id"],
                        "to_id": b["id"],
                        "from_site": a["site"],
                        "to_site": b["site"],
                        "detail": reason,
                        "strength": "ALTA",
                    })

            # 2) Avatar compartido (misma URL de imagen pública)
            if a["avatar"] and a["avatar"] == b["avatar"]:
                reason = f"Mismo avatar compartido entre {a['site']} y {b['site']}"
                profile_links_map[a["id"]].append(("avatar", b["site"], reason))
                profile_links_map[b["id"]].append(("avatar", a["site"], reason))
                if (*pair_key, "avatar") not in pair_seen:
                    pair_seen.add((*pair_key, "avatar"))
                    cross_links.append({
                        "type": "avatar",
                        "from_id": a["id"],
                        "to_id": b["id"],
                        "from_site": a["site"],
                        "to_site": b["site"],
                        "detail": reason,
                        "strength": "ALTA",
                    })

            # 3) Similitud de biografía (Jaccard >= 0.30 con al menos 2 palabras compartidas)
            common_bio = a["bio_toks"] & b["bio_toks"]
            if len(common_bio) >= 2 and _jaccard(a["bio_toks"], b["bio_toks"]) >= 0.30:
                sample = ", ".join(sorted(common_bio)[:4])
                reason = f"Biografía similar entre {a['site']} y {b['site']} ({sample})"
                profile_links_map[a["id"]].append(("bio_sim", b["site"], reason))
                profile_links_map[b["id"]].append(("bio_sim", a["site"], reason))
                if (*pair_key, "bio_sim") not in pair_seen:
                    pair_seen.add((*pair_key, "bio_sim"))
                    cross_links.append({
                        "type": "bio_sim",
                        "from_id": a["id"],
                        "to_id": b["id"],
                        "from_site": a["site"],
                        "to_site": b["site"],
                        "detail": reason,
                        "strength": "MEDIA",
                    })

            # 4) Mismo nombre visible multi-token entre perfiles
            common_name = a["name_toks"] & b["name_toks"]
            if len(common_name) >= 2:
                reason = f"Mismo nombre visible entre {a['site']} y {b['site']}"
                profile_links_map[a["id"]].append(("name_match", b["site"], reason))
                profile_links_map[b["id"]].append(("name_match", a["site"], reason))
                if (*pair_key, "name_match") not in pair_seen:
                    pair_seen.add((*pair_key, "name_match"))
                    cross_links.append({
                        "type": "name_match",
                        "from_id": a["id"],
                        "to_id": b["id"],
                        "from_site": a["site"],
                        "to_site": b["site"],
                        "detail": reason,
                        "strength": "MEDIA",
                    })

    # Puntaje por perfil
    evaluated = []
    warnings = []
    distinct_locations = set()

    for item in prep:
        p = item["raw"]
        manual = (p.get("manual_status") or "auto").lower()
        reasons = []
        score = 0

        # A) Confianza OSINT base y multi-herramienta
        osint_conf = (p.get("osint_confidence") or "BAJA").upper()
        sources_list = [s.strip() for s in re.split(r"[+,;/]", p.get("sources") or "") if s.strip()]
        if osint_conf == "ALTA":
            score += 20
            reasons.append("Confianza OSINT ALTA en el hallazgo original (+20)")
        elif osint_conf == "MEDIA":
            score += 12
            reasons.append("Confianza OSINT MEDIA (+12)")
        else:
            score += 5
            reasons.append("Confianza OSINT BAJA (+5)")

        if len(set(s.lower() for s in sources_list)) >= 2:
            score += 15
            reasons.append(f"Confirmado por múltiples herramientas ({', '.join(sources_list)}) (+15)")

        # B) Username frente a semillas del caso y especificidad
        is_generic = False
        if item["username"]:
            pts, msg, is_generic = _username_specificity(item["username"])
            if item["username"] in seed_usernames:
                score += pts
                reasons.append(f"{msg} y coincide con semilla del caso (+{pts})")
            else:
                # Variante parcial
                partial = any(item["username"] in su or su in item["username"] for su in seed_usernames)
                if partial:
                    half = max(4, pts // 2)
                    score += half
                    reasons.append(f"Username variante de semilla del caso (+{half})")
                elif not seed_usernames:
                    score += pts
                    reasons.append(f"{msg} (+{pts})")

        # C) Señales cruzadas con otros perfiles del caso
        links = profile_links_map.get(item["id"]) or []
        link_types_added = set()
        for ltype, other_site, detail in links:
            if ltype in link_types_added:
                continue
            link_types_added.add(ltype)
            if ltype == "cross_link":
                score += 28
                reasons.append(f"{detail} (+28)")
            elif ltype == "avatar":
                score += 22
                reasons.append(f"{detail} (+22)")
            elif ltype == "bio_sim":
                score += 16
                reasons.append(f"{detail} (+16)")
            elif ltype == "name_match":
                score += 14
                reasons.append(f"{detail} (+14)")

        # D) Coincidencia de nombre visible contra sujeto o semillas de nombre
        if item["name_toks"] and seed_names:
            best_overlap = max((len(item["name_toks"] & sn) for sn in seed_names), default=0)
            if best_overlap >= 2:
                score += 18
                reasons.append("Nombre visible coincide con nombre completo del caso (+18)")
            elif best_overlap == 1 and len(item["name_toks"]) >= 1:
                score += 8
                reasons.append("Coincidencia parcial de nombre visible (+8)")

        # E) Coincidencia de ubicación
        if item["loc_toks"]:
            distinct_locations.add(normalize_text(p.get("location") or ""))
            if seed_locations and (item["loc_toks"] & seed_locations):
                score += 14
                reasons.append(f"Ubicación coincide con la zona del caso ({p.get('location')}) (+14)")
            elif any(location_counts.get(lt, 0) >= 2 for lt in item["loc_toks"]):
                score += 10
                reasons.append(f"Ubicación consistente con otro perfil ({p.get('location')}) (+10)")
            elif seed_locations:
                # Ubicación declarada distinta a la semilla -> señal de revisión
                score = max(0, score - 8)
                reasons.append(f"Ubicación declarada ('{p.get('location')}') difiere de la zona del caso (-8)")
                warnings.append(
                    f"Posible homónimo en {item['site']}: declara ubicación '{p.get('location')}', diferente a la zona registrada en el caso."
                )

        # F) Correo o dominio corporativo/personal mencionado en bio o website
        bio_web = normalize_text(f"{p.get('bio') or ''} {p.get('website') or ''}")
        if any(em in bio_web for em in seed_emails):
            score += 25
            reasons.append("Menciona correo electrónico registrado en el caso (+25)")
        elif any(dom in bio_web for dom in seed_domains):
            score += 15
            reasons.append("Referencia dominio asociado al caso (+15)")

        # G) Alerta por homonimia cuando el username es genérico y no hay metadatos de respaldo
        has_corroboration = bool(link_types_added) or bool(item["bio_toks"]) or bool(item["name_toks"])
        if is_generic and not has_corroboration:
            warnings.append(
                f"Perfil en {item['site']} usa un username común ('{item['username']}') sin metadatos públicos que lo corroboren; verificar manualmente."
            )

        score = max(0, min(100, int(score)))

        # Respetar decisión manual del analista (human-in-the-loop)
        if manual == "confirmed":
            level = "VERIFICADO"
            score = max(score, 95)
            reasons.insert(0, "Confirmado manualmente por el analista")
        elif manual == "rejected":
            level = "DESCARTADO"
            score = 0
            reasons.insert(0, "Descartado manualmente como falso positivo / homónimo")
        else:
            if score >= 65:
                level = "VERIFICADO"
            elif score >= 40:
                level = "PROBABLE"
            else:
                level = "REVISAR"

        evaluated.append({
            "profile_id": item["id"],
            "score": score,
            "level": level,
            "reasons": reasons,
        })

    if len(distinct_locations) >= 3:
        warnings.append(
            f"Se detectaron {len(distinct_locations)} ubicaciones distintas entre los perfiles del caso ({', '.join(sorted(distinct_locations)[:4])}). Revisa posibles cuentas de terceros."
        )

    # Deduplicar advertencias manteniendo orden
    warnings_unique = list(dict.fromkeys(warnings))

    active_scores = [e["score"] for e in evaluated if e["level"] != "DESCARTADO"]
    stats = {
        "total": len(evaluated),
        "verified": sum(1 for e in evaluated if e["level"] == "VERIFICADO"),
        "probable": sum(1 for e in evaluated if e["level"] == "PROBABLE"),
        "review": sum(1 for e in evaluated if e["level"] == "REVISAR"),
        "rejected": sum(1 for e in evaluated if e["level"] == "DESCARTADO"),
        "cross_links_count": len(cross_links),
        "avg_score": round(sum(active_scores) / len(active_scores), 1) if active_scores else 0.0,
    }

    return {
        "profiles": evaluated,
        "cross_links": cross_links,
        "warnings": warnings_unique,
        "stats": stats,
    }
