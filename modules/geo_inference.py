# -*- coding: utf-8 -*-
"""
Motor de Inferencia Geográfica Probabilística (Módulo C) para OSINT-Auto.

Fusiona:
  1. `geo_hints` extraídos de imágenes (EXIF GPS, VLM, OCR, flora, arquitectura, objetos).
  2. Metadatos EXIF/GPS de `media_items` (`media_meta.py`).
  3. Lugares detectados en Fase 4 (NER + `published_location` en publicaciones).

Reglas obligatorias:
  - Lenguaje estrictamente probabilístico ("compatible con", "posible").
  - Prioridad máxima cuando existe coordenada EXIF explícita (`source='exif'`).
  - Sin geolocalización inversa por defecto (`GEO_NOMINATIM_ENABLED=false`).
"""
import os
import re
from typing import Optional

import requests

# Mapeo probabilístico ligero de pistas visuales/contextuales a regiones/ciudades compatibles
_VISUAL_REGION_RULES = [
    (["transmilenio", "sitp", "bogota", "chapinero", "ladrillo rojo"], "Colombia", "Cundinamarca", "Bogotá"),
    (["cartagena", "balcones coloniales", "ciudad amurallada"], "Colombia", "Bolívar", "Cartagena"),
    (["medellin", "metro", "antioquia", "paisa"], "Colombia", "Antioquia", "Medellín"),
    (["frailejones / páramo", "paramo", "neblina andina"], "Colombia / Región Andina", "Zona Andina", ""),
    (["palmeras", "playa", "cocotero"], "Zona Costera / Tropical", "Litoral Caribe/Pacífico", ""),
    (["cdmx", "reforma", "metrobus", "polanco"], "México", "Ciudad de México", "Ciudad de México"),
    (["madrid", "gran via", "retiro", "espana"], "España", "Comunidad de Madrid", "Madrid"),
    (["buenos aires", "obelisco", "palermo", "argentina"], "Argentina", "Buenos Aires", "Buenos Aires"),
]


# Centroides aproximados para ubicar candidatos en el mapa probabilístico (sin APIs externas)
_KNOWN_CENTROIDS = {
    "bogota": (4.7110, -74.0721),
    "medellin": (6.2442, -75.5812),
    "cartagena": (10.3910, -75.4794),
    "cali": (3.4516, -76.5320),
    "barranquilla": (10.9685, -74.7813),
    "santa marta": (11.2408, -74.1990),
    "bucaramanga": (7.1193, -73.1227),
    "pereira": (4.8087, -75.6906),
    "zona andina": (5.5000, -74.2000),
    "litoral caribe": (10.8000, -75.0000),
    "colombia": (4.5709, -74.2973),
    "ciudad de mexico": (19.4326, -99.1332),
    "cdmx": (19.4326, -99.1332),
    "mexico": (23.6345, -102.5528),
    "madrid": (40.4168, -3.7038),
    "barcelona": (41.3874, 2.1686),
    "espana": (40.4637, -3.7492),
    "buenos aires": (-34.6037, -58.3816),
    "argentina": (-38.4161, -63.6167),
    "lima": (-12.0464, -77.0428),
    "peru": (-9.1900, -75.0152),
    "santiago": (-33.4489, -70.6693),
    "chile": (-35.6751, -71.5430),
    "quito": (-0.1807, -78.4678),
    "ecuador": (-1.8312, -78.1834),
    "caracas": (10.4806, -66.9036),
    "venezuela": (6.4238, -66.5897),
    "miami": (25.7617, -80.1918),
    "new york": (40.7128, -74.0060),
    "estados unidos": (37.0902, -95.7129),
    "london": (51.5072, -0.1276),
    "paris": (48.8566, 2.3522),
}


# Para qué sirve: elimina tildes y convierte a minúsculas para comparar topónimos contra centroides.
# Cómo funciona: descompone en NFD con `unicodedata` y descarta marcas diacríticas (`Mn`).
def _strip_accents(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn").lower()


# Para qué sirve: asigna coordenadas y precisión estimada (`exif` o centroide aproximado) a una ubicación candidata.
# Cómo funciona: si `is_exif=True` extrae `lat,lon` por regex; si no, busca coincidencia en `_KNOWN_CENTROIDS`.
def _resolve_candidate_coords(label: str, country: str, region: str, city: str, is_exif: bool = False) -> tuple:
    """Devuelve `(lat, lon, precision)` desde EXIF o centroide aproximado conocido."""
    if is_exif and "," in (city or label):
        src_s = city if "," in (city or "") else label
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)", src_s)
        if m:
            return float(m.group(1)), float(m.group(2)), "exif"
    for cand in (city, region, country, label):
        norm = _strip_accents(cand)
        if not norm:
            continue
        for key, (lat, lon) in _KNOWN_CENTROIDS.items():
            if key in norm:
                return lat, lon, "approximate_centroid"
    return None, None, "none"


# Para qué sirve: verifica si está permitida la geocodificación inversa externa con OpenStreetMap Nominatim.
# Cómo funciona: prioriza `override` explícito y si es `None` evalúa `GEO_NOMINATIM_ENABLED` (apagado por defecto).
def nominatim_enabled(override: Optional[bool] = None) -> bool:
    """Indica si Nominatim está habilitado (opt-in explícito por parámetro o `GEO_NOMINATIM_ENABLED`)."""
    if override is not None:
        return bool(override)
    return (os.getenv("GEO_NOMINATIM_ENABLED", "false") or "false").strip().lower() in ("1", "true", "yes", "si")


# Para qué sirve: traduce coordenadas GPS EXIF a país, región y ciudad vía Nominatim cuando el analista lo habilita.
# Cómo funciona: consulta `/reverse` mediante `cached_get_json` (TTL 24 h, coordenadas redondeadas a 4 decimales).
def _reverse_nominatim(lat: float, lon: float) -> Optional[dict]:
    """Consulta opcional (opt-in estricto) a OpenStreetMap Nominatim con caché TTL en SQLite."""
    from modules.http_client import cached_get_json

    res = cached_get_json(
        source="nominatim_reverse",
        url="https://nominatim.openstreetmap.org/reverse",
        query_key=f"{round(float(lat), 4)},{round(float(lon), 4)}",
        params={"format": "jsonv2", "lat": lat, "lon": lon, "accept-language": "es"},
        headers={"User-Agent": "OSINT-Auto/2.5 (authorized-geo-inference)"},
        timeout=6,
        ttl_seconds=86400,
        max_retries=1,
        requester=requests.get,
    )
    if res["ok"] and isinstance(res.get("data"), dict):
        data = res["data"]
        addr = data.get("address") or {}
        return {
            "country": addr.get("country") or "",
            "region": addr.get("state") or addr.get("region") or "",
            "city": addr.get("city") or addr.get("town") or addr.get("village") or "",
            "display_name": data.get("display_name") or "",
        }
    return None


# Para qué sirve: registra o refuerza una zona geográfica candidata acumulando evidencia y confianza sin duplicados.
# Cómo funciona: agrupa en `buckets` por etiqueta en minúscula, prioriza EXIF o refuerza convergencia (+25% de `conf`, tope 0.92).
def _add_candidate(buckets: dict, country: str, region: str, city: str, conf: float, source: str, evidence: str, is_exif: bool = False):
    label_parts = [p for p in (city, region, country) if p]
    if not label_parts:
        return
    label = ", ".join(label_parts)
    key = label.lower()
    level = "city" if city else ("region" if region else "country")
    entry = buckets.get(key)
    if not entry:
        lat, lon, prec = _resolve_candidate_coords(label, country, region, city, is_exif=is_exif)
        entry = {
            "label": label,
            "country": country,
            "region": region,
            "city": city,
            "level": level,
            "confidence": round(min(1.0, max(0.0, conf)), 3),
            "source": source,
            "has_exif": is_exif,
            "lat": lat,
            "lon": lon,
            "coord_precision": prec,
            "evidence": [],
        }
        buckets[key] = entry
    else:
        # Reforzar confianza cuando convergen varias fuentes independientes
        if is_exif:
            entry["has_exif"] = True
            entry["source"] = "exif"
            entry["confidence"] = max(entry["confidence"], round(min(1.0, conf), 3))
        else:
            boosted = min(0.92, entry["confidence"] + conf * 0.25)
            entry["confidence"] = round(max(entry["confidence"], boosted), 3)
    if evidence and evidence not in entry["evidence"] and len(entry["evidence"]) < 5:
        entry["evidence"].append(evidence)


# Para qué sirve: extrae latitud y longitud numéricas de un diccionario de elemento multimedia.
# Cómo funciona: lee `gps_lat`/`gps_lon` directos y hace fallback a `metadata.gps` (`lat`/`latitude`, `lon`/`longitude`).
def extract_media_gps(m: dict) -> Optional[dict]:
    """Obtiene coordenadas GPS EXIF desde `gps_lat`/`gps_lon` o desde `metadata.gps`."""
    if not isinstance(m, dict):
        return None
    if m.get("gps_lat") is not None and m.get("gps_lon") is not None:
        try:
            return {"lat": float(m["gps_lat"]), "lon": float(m["gps_lon"])}
        except (TypeError, ValueError):
            pass
    meta = m.get("metadata") or {}
    gps = meta.get("gps") if isinstance(meta, dict) else None
    if isinstance(gps, dict):
        lat = gps.get("lat") if gps.get("lat") is not None else gps.get("latitude")
        lon = gps.get("lon") if gps.get("lon") is not None else gps.get("longitude")
        if lat is not None and lon is not None:
            try:
                return {"lat": float(lat), "lon": float(lon)}
            except (TypeError, ValueError):
                pass
    return None


# Para qué sirve: produce el ranking probabilístico de ubicaciones del caso a partir de pistas visuales, EXIF y texto.
# Cómo funciona: procesa primero GPS EXIF (con Nominatim opcional), aplica `_VISUAL_REGION_RULES`, añade `published_location`/NER y redacta enunciados cautelosos.
def infer_case_locations(
    geo_hints: list,
    media_items: Optional[list] = None,
    posts: Optional[list] = None,
    nlp_data: Optional[dict] = None,
    use_nominatim: Optional[bool] = None,
) -> dict:
    """
    Fusiona `geo_hints` + EXIF/GPS + NER/ubicaciones publicadas en un ranking probabilístico
    `country -> region -> city` con confianza y evidencia literal.
    """
    buckets = {}
    nom_on = nominatim_enabled(use_nominatim)
    nominatim_hints = []

    # 1) Pistas EXIF GPS (máxima prioridad cuando existen)
    for m in media_items or []:
        gps = extract_media_gps(m) or {}
        lat = gps.get("lat")
        lon = gps.get("lon")
        if lat is not None and lon is not None:
            coord_str = f"{lat},{lon}"
            if nom_on:
                rev = _reverse_nominatim(float(lat), float(lon))
                if rev and (rev.get("country") or rev.get("city")):
                    _add_candidate(
                        buckets,
                        rev.get("country") or "",
                        rev.get("region") or "",
                        rev.get("city") or "",
                        0.96,
                        "nominatim",
                        f"EXIF GPS ({coord_str}) + Nominatim: {rev.get('display_name') or ''}",
                        is_exif=True,
                    )
                    nominatim_hints.append({
                        "media_id": m.get("id"),
                        "hint_type": "exif",
                        "value": rev.get("display_name") or coord_str,
                        "confidence": 0.96,
                        "evidence_quote": f"Nominatim sobre EXIF GPS ({coord_str})",
                        "source": "nominatim",
                    })
                    continue
            _add_candidate(
                buckets,
                "Coordenadas EXIF explícitas",
                "",
                coord_str,
                0.98,
                "exif",
                f"EXIF GPS en {m.get('filename') or 'imagen'}: {coord_str}",
                is_exif=True,
            )

    for gh in geo_hints or []:
        htype = (gh.get("hint_type") or "").lower()
        val = (gh.get("value") or "").strip()
        conf = float(gh.get("confidence") or 0.4)
        src = gh.get("source") or "local_context"
        ev = gh.get("evidence_quote") or f"{htype}: {val}"
        if htype == "exif" and val:
            if nom_on and "," in val:
                try:
                    lat_s, lon_s = val.split(",", 1)
                    rev = _reverse_nominatim(float(lat_s.strip()), float(lon_s.strip()))
                    if rev and (rev.get("country") or rev.get("city")):
                        _add_candidate(
                            buckets,
                            rev.get("country") or "",
                            rev.get("region") or "",
                            rev.get("city") or "",
                            0.96,
                            "nominatim",
                            f"{ev} · Nominatim: {rev.get('display_name') or ''}",
                            is_exif=True,
                        )
                        continue
                except Exception:
                    pass
            _add_candidate(buckets, "Coordenadas EXIF explícitas", "", val, max(0.95, conf), "exif", ev, is_exif=True)
            continue

        low = f"{val} {ev}".lower()
        matched_rule = False
        for kws, country, region, city in _VISUAL_REGION_RULES:
            if any(k in low for k in kws):
                _add_candidate(buckets, country, region, city, conf * 0.85, src, ev, is_exif=False)
                matched_rule = True
        if not matched_rule and htype in ("ocr", "flora", "architecture", "object") and val:
            _add_candidate(
                buckets,
                f"Entorno compatible con pista visual ({val})",
                "",
                "",
                min(0.60, conf * 0.7),
                src,
                ev,
            )

    # 2) Ubicaciones declaradas en posts (`published_location`) y NER de Fase 4
    for p in posts or []:
        loc = (p.get("published_location") or "").strip()
        if loc:
            _add_candidate(
                buckets,
                loc,
                "",
                "",
                0.76,
                "post_location",
                f"Ubicación declarada en publicación: '{loc}'",
            )

    places = ((nlp_data or {}).get("ner") or {}).get("places") or []
    for pl in places:
        if isinstance(pl, dict):
            name = (pl.get("text") or pl.get("label") or "").strip()
            cnt = int(pl.get("count") or 1)
        else:
            name = str(pl).strip()
            cnt = 1
        if name and len(name) >= 3:
            _add_candidate(
                buckets,
                name,
                "",
                "",
                min(0.75, 0.42 + cnt * 0.08),
                "ner",
                f"Mención geográfica NER en textos ({cnt}x): '{name}'",
            )

    candidates = list(buckets.values())
    candidates.sort(key=lambda c: (-int(c["has_exif"]), -c["confidence"], -len(c["evidence"]), c["label"]))

    for c in candidates:
        pct = int(round(c["confidence"] * 100))
        if c["has_exif"]:
            c["probabilistic_statement"] = (
                f"Compatible con ubicación registrada en metadatos EXIF ({pct}% confianza)."
            )
        else:
            c["probabilistic_statement"] = (
                f"Posible zona compatible con pistas visuales/contextuales ({pct}% confianza; requiere validación humana)."
            )

    return {
        "nominatim_enabled": nom_on,
        "candidates": candidates,
        "nominatim_hints": nominatim_hints,
        "hints_count": len(geo_hints or []),
    }
